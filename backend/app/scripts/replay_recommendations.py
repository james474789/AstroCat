"""
Replay the recommendation engine over historical imaging nights (R1 spec §5, §14).

    python -m app.scripts.replay_recommendations [--since YYYY-MM-DD] [--until YYYY-MM-DD]
        [--moon-bb D[,W]] [--grid] [--grid-moon] [--grid-max N] [--out PATH] [--seed N]

Read-only. Loads history, catalog, sites and rigs once, then runs the pure
replay (app/services/recommend/replay.py). Headline metrics are on HOME +
UNKNOWN_SITE pairs; REMOTE pairs are reported separately. The JSON report is
written to --out (if given) and always to <log_dir>/replay_latest.json, which
GET /api/recommendations/replay/latest serves.

--grid runs the tuning grid (replay.TUNING_SPACE, 288 combinations; --grid-max
samples fewer) and prints the top 5 by hit@5 (MRR tie-break) with the recency
baseline on the same pairs. --grid-moon first tries broadband/OSC Moon D in
{60, 90, 120} at the default weights and runs the weight grid with the best.
Nothing is applied.
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger("replay_recommendations")


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _moon_bb(s: str):
    parts = [float(x) for x in s.split(",")]
    if not 1 <= len(parts) <= 2:
        raise argparse.ArgumentTypeError("expected D or D,W")
    return parts


def load_replay_data():
    from app.database import SessionLocal
    from app.services.recommend import loader
    from app.services.recommend.replay import ReplayData, classify_pairs

    r = loader._redis()
    with SessionLocal() as s:
        hist = loader.query_history_inputs(s)
        data = loader.load_pool_data(s, hist)
        records = loader.load_rigs(s, data.rows)
        sites = loader.load_sites(s)
        default = loader.pick_site(sites, None)
        horizons = {site.id: loader.site_horizon(s, site, r) for site in sites}
        pair_rows = loader.query_pair_rows(s)
    pair_info = classify_pairs(pair_rows, [site.latitude for site in sites], data.key_map)
    return ReplayData(
        pool=data.pool, rows=data.rows, masters=data.masters, goal_rows=data.goal_rows,
        sites={site.id: site for site in sites}, default_site_id=default.id, horizons=horizons,
        rig_specs={rec.id: rec.spec for rec in records if rec.spec is not None},
        active_rig_ids=[rec.id for rec in records if rec.is_active],
        pair_info=pair_info, fold_stats=data.fold_stats(hist.rows),
    ), records


def main(argv=None) -> int:
    from dataclasses import replace

    from app.services.recommend import Params
    from app.services.recommend.loader import replay_latest_path
    from app.services.recommend.replay import (
        build_report, format_grid, format_table, grid_search, moon_bb_grid, replay_nights, run_replay, tuning_grid,
    )

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=_date)
    ap.add_argument("--until", type=_date)
    ap.add_argument("--moon-bb", type=_moon_bb, metavar="D[,W]",
                    help="broadband/OSC Moon rule for this run (default 120,14)")
    ap.add_argument("--grid", action="store_true", help="tuning grid (top 5 by hit@5)")
    ap.add_argument("--grid-moon", action="store_true", help="with --grid: stage 1 over BB/OSC Moon D 60/90/120")
    ap.add_argument("--grid-max", type=int, help="with --grid: sample at most N weight combinations (seeded)")
    ap.add_argument("--out", type=Path, help="also write the JSON report here")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    started = time.time()
    data, records = load_replay_data()
    nights = replay_nights(data.rows, args.since, args.until)
    print(f"Loaded {len(data.rows)} history rows, {len(data.pool)} candidates, {len(data.rig_specs)} usable rigs, "
          f"{len(nights)} nights in {time.time() - started:.1f}s")
    print(f"Folded {data.fold_stats.get('keys', 0)} stray keys ({data.fold_stats.get('rows', 0)} history rows) "
          f"into pool keys")
    skipped = [f"{rec.name} ({rec.skip_reason})" for rec in records if rec.spec is None]
    if skipped:
        print("Rigs skipped: " + "; ".join(skipped))
    if not nights:
        print("No nights to replay.")
        return 1

    params = Params()
    if args.moon_bb:
        rules = dict(params.moon_rules)
        for cls in ("BB", "OSC"):
            w = args.moon_bb[1] if len(args.moon_bb) > 1 else rules[cls][1]
            rules[cls] = (args.moon_bb[0], w)
        params = replace(params, moon_rules=rules)

    def progress(n, total):
        if n % 25 == 0 or n == total:
            print(f"  {n}/{total} nights ({time.time() - started:.0f}s)", flush=True)

    warm = {} if args.grid else None      # reuse the per-night state in the grid
    outcomes = run_replay(nights, data.inputs_for, params, progress=progress, pair_info=data.pair_info, keep=warm)
    extra = {"since": args.since.isoformat() if args.since else None,
             "until": args.until.isoformat() if args.until else None,
             "folded_keys": data.fold_stats, "runtime_seconds": None}
    if args.grid:
        combos = tuning_grid(base=params.weights, max_combos=args.grid_max, seed=args.seed)
        print(f"Grid search over {len(combos)} combinations (nothing is applied)...")
        grid = grid_search(nights, data.inputs_for, params, combos=combos,
                           moon_rules=moon_bb_grid(params.moon_rules) if args.grid_moon else None,
                           progress=print, pair_info=data.pair_info, warm_keep=warm)
        extra["grid"] = grid
        extra["grid_top"] = grid["top"]
        print(format_grid(grid))
    extra["runtime_seconds"] = round(time.time() - started, 1)
    report = build_report(outcomes, params, datetime.utcnow(), seed=args.seed, extra=extra)
    print(format_table(report))

    text = json.dumps(report, indent=2, default=str)
    latest = replay_latest_path()
    for path in filter(None, [args.out, latest]):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"Report written to {path}")
        except OSError as e:
            print(f"Could not write {path}: {e}", file=sys.stderr)
    print(f"Done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
