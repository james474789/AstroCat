"""
Replay the recommendation engine over historical imaging nights (R1 spec §5).

    python -m app.scripts.replay_recommendations [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                                 [--grid] [--grid-moon] [--out PATH] [--seed N]

Read-only. Loads history, catalog, sites and rigs once, then runs the pure
replay (app/services/recommend/replay.py). The JSON report is written to
--out (if given) and always to <log_dir>/replay_latest.json, which
GET /api/recommendations/replay/latest serves. --grid prints the top 5 weight
settings by hit@5 (MRR tie-break); nothing is applied.
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


def load_replay_data():
    from app.database import SessionLocal
    from app.services.recommend import loader
    from app.services.recommend.replay import ReplayData

    r = loader._redis()
    with SessionLocal() as s:
        hist = loader.query_history_inputs(s)
        pool = loader.load_pool(s, hist.imaged_keys)
        records = loader.load_rigs(s, hist.rows)
        sites = loader.load_sites(s)
        default = loader.pick_site(sites, None)
        horizons = {site.id: loader.site_horizon(s, site, r) for site in sites}
    return ReplayData(
        pool=pool, rows=hist.rows, masters=hist.masters, goal_rows=hist.goal_rows,
        sites={site.id: site for site in sites}, default_site_id=default.id, horizons=horizons,
        rig_specs={rec.id: rec.spec for rec in records if rec.spec is not None},
        active_rig_ids=[rec.id for rec in records if rec.is_active],
    ), records


def main(argv=None) -> int:
    from app.services.recommend.loader import replay_latest_path
    from app.services.recommend import Params
    from app.services.recommend.replay import (
        build_report, format_table, grid_search, moon_rule_grid, replay_nights, run_replay,
    )

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=_date)
    ap.add_argument("--until", type=_date)
    ap.add_argument("--grid", action="store_true", help="coarse grid over the weights (top 5 by hit@5)")
    ap.add_argument("--grid-moon", action="store_true", help="with --grid: also vary Moon-rule D by +/-25%%")
    ap.add_argument("--out", type=Path, help="also write the JSON report here")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    started = time.time()
    data, records = load_replay_data()
    nights = replay_nights(data.rows, args.since, args.until)
    print(f"Loaded {len(data.rows)} history rows, {len(data.pool)} candidates, {len(data.rig_specs)} usable rigs, "
          f"{len(nights)} nights in {time.time() - started:.1f}s")
    skipped = [f"{rec.name} ({rec.skip_reason})" for rec in records if rec.spec is None]
    if skipped:
        print("Rigs skipped: " + "; ".join(skipped))
    if not nights:
        print("No nights to replay.")
        return 1

    params = Params()

    def progress(n, total):
        if n % 25 == 0 or n == total:
            print(f"  {n}/{total} nights ({time.time() - started:.0f}s)", flush=True)

    outcomes = run_replay(nights, data.inputs_for, params, progress=progress)
    extra = {"since": args.since.isoformat() if args.since else None,
             "until": args.until.isoformat() if args.until else None,
             "runtime_seconds": None}
    if args.grid:
        print("Grid search (nothing is applied)...")
        rules = moon_rule_grid(params.moon_rules) if args.grid_moon else None
        top = grid_search(nights, data.inputs_for, params, moon_rules=rules, progress=print)
        extra["grid_top"] = top
        print("Top settings by hit@5 (MRR tie-break):")
        for i, row in enumerate(top, start=1):
            print(f"  {i}. hit@5={row['metrics']['hit@5']:.3f} mrr={row['metrics']['mrr']:.3f} "
                  f"weights={row['weights']}" + (f" moon={row['moon_rules']}" if args.grid_moon else ""))
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
