# Tonight: rig-aware target picking (size window, wide-field targets, "nothing good" rigs)

## Context

On 2026-09-30 the Tonight page's "Plan per mounted rig" gave **M92** to the **Sigma 105mm - Canon 6D** rig
(field 20.4° × 13.6°, 13.4″/px). M92 is 14.4′, so it fills about 2% of the short side. The live cached
payload shows why:

- **Framing isn't a real constraint.** TOO_SMALL is `< 15 px`, and M92 is about 65 px. TOO_BIG is `> 4×` the
  short side. Framing is only an additive term with weight 0.2, while project, prior and urgency are the same
  on every rig. M92 scored 0.384 on the 6D with a framing fit of about 0.
- **The rig plan always gives each rig a primary.** The Moon is 78% lit and up all night, and OSC/broadband
  needs 120° of separation, so the 6D had nothing good. It got the least-bad option (1.7 h available).
- **The candidate pool has few targets that suit a 13° field** (Messier/NGC/Caldwell/Sh2 are mostly under 1°).

User decisions:
- Each rig gets an editable target-size window. For the 6D + 105mm it is **3°–16°**. Defaults come from the
  field: 22% of the short side up to 80% of the long side.
- A target already imaged on a rig (or on a rig with a similar pixel scale) is **exempt** from the window there,
  which keeps North America on the ZS73R.
- A rig with nothing good shows **"nothing good tonight" plus the reason**, not a forced pick.
- **Add a curated wide-field target list.**
- The project/"committed" rule stays as it is.

## 1. Per-rig size window (DB + Equipment)

- **Model** `backend/app/models/equipment.py` `Rig`: add nullable `min_target_arcmin`, `max_target_arcmin`
  (Float). NULL means "use the default from the field of view".
- **Migration**: a new file after `c2e4a8d95012` (star_quality_metrics). It is defensive (`sa.inspect` before
  `add_column`), following the existing pattern.
- **Schemas** `backend/app/schemas/equipment.py`: add both fields to `RigCreate` / `RigUpdate` / the rig output
  (`Field(None, gt=0, le=3600)`). The output also gets `target_window_arcmin: [min, max]`, the effective values
  including defaults, so the UI can show them.
- **API** (the equipment rig create/update handlers): persist the fields. The existing equipment-change hook
  already drops `recs:*`. Check that it runs on the rig PATCH as well.
- **Frontend** `frontend/src/pages/Equipment.jsx` rig form (~l.250–345): add a "Target size" row with min and max
  inputs in **degrees**, stored as arcmin. The placeholders are the defaults from `preview.fov`, and clearing
  an input restores the default. The rig row (~l.196) shows the window next to the FOV.

## 2. Engine: the window, exemption and per-rig summary

`backend/app/services/recommend/scoring.py`
- `RigSpec` gains `min_target_arcmin: Optional[float]` and `max_target_arcmin: Optional[float]`, plus a
  property `size_window()` that returns the declared value or the default. The defaults are
  `WINDOW_MIN_SHORT_FRACTION = 0.22 × short side` and `WINDOW_MAX_LONG_FRACTION = 0.80 × long side`.
- In `evaluate_rig`:
  - TOO_SMALL becomes `size < window_min OR px < MIN_TARGET_PX`.
  - TOO_BIG becomes `size > window_max`. This replaces `MAX_FILL_RATIO`.
  - Unknown size still passes, as today.
  - **Exemption:** the size rules are skipped where `imaged_on_rig[i]` is true.
- `HistoryArrays` gains `rig_ids` (a list of sets) and `scales` (a list of sets of per-row scales). `history_arrays`
  fills them from `TargetHistory`. `evaluate_rig` computes
  `imaged_on_rig = rig.id in rig_ids[i] or any(|s / rig.scale − 1| ≤ 0.25 for s in scales[i])`.
- `backend/app/services/recommend/history.py` `TargetHistory`: add `scales: Set[float]`, filled from each
  row's `best_scale`. Keep `best_scale` (min) for its current users.
- `backend/app/services/recommend/loader.py` `rig_spec` / `load_rigs`: pass the two new rig columns through.
  The inputs cache has no rigs in it, so no key change is needed.
- `RigEval` already carries `excluded`. In `recommend()`, build `rig_summary`: for each rig, the feasible count
  and exclusion counts from `ev.excluded` (the same codes as `EXCLUDED_CODES`).

`backend/app/services/recommend/__init__.py`
- `result_payload`: add `rig_summary` and bump `PAYLOAD_FORMAT` to 4, so old cache entries are ignored.
- `assign_rig_plan`: add a quality bar. A (target, rig) pair is eligible only if its available hours on that
  rig are at least `MARGINAL_MIN_HOURS` (1.5 h, from `lanes.py`). Use `alt["available_hours"]`, or the pick's
  value for its best rig. A rig with no eligible pair gets no items.
- `render_payload`: each `rig_plan` entry gains:
  - `verdict`: `lanes.verdict()` applied to the rig's primary, re-labelled for that rig.
  - `note`: set when the rig has no items. It is built from `rig_summary` plus the Moon and tier context. For
    example:
    - "Moon 78% lit and up all night: broadband/OSC needs 120°; nothing clears it for 1.5 h"
      (the main exclusion is MOON, or everything feasible is under 1.5 h)
    - "Nothing between 3° and 16° is well placed tonight" (the main exclusions are the size rules or the
      horizon)
  - The rig's size window, for display.
- `explain_target` / `pair_details`: add `size_window_arcmin` and `imaged_on_rig`, so the target explanation
  shows why a pair was dropped.

## 3. Curated wide-field candidates

- New pure module `backend/app/services/recommend/widefield.py`. It holds a tuple of `WideField(key, name,
  ra_deg, dec_deg, size_arcmin, kind, members)`, about 25 entries, with keys prefixed `WF_`. Kinds follow the
  existing rules: EMISSION only for HII-dominated fields. Starting list (verify the centres and sizes when
  building it):
  - Cygnus / Sadr region, 8°, EMISSION
  - North America + Pelican + Deneb, 6°, EMISSION
  - Whole Cygnus Milky Way, 18°, EMISSION
  - Heart, Soul & Double Cluster, 8°, EMISSION
  - Cassiopeia Milky Way, 16°, EMISSION
  - Cepheus: IC1396 / Elephant's Trunk, 5°, EMISSION
  - Cepheus: Wizard / Bubble / Cave (NGC7380, NGC7635, Sh2-155), 6°, EMISSION
  - Iris + Cepheus dust, 4°, REFLECTION
  - LDN 1235 / Shark region, 5°, REFLECTION
  - Orion Belt & Sword, 6°, EMISSION
  - Barnard's Loop, 12°, EMISSION
  - Whole Orion, 18°, EMISSION
  - California + Pleiades, 12°, OTHER
  - Taurus dark clouds, 10°, OTHER
  - Hyades + Pleiades, 15°, CLUSTER
  - Auriga (Flaming Star, Tadpole, M36/37/38), 6°, EMISSION
  - Rosette + Cone, 5°, EMISSION
  - Seagull (IC2177), 4°, EMISSION
  - Andromeda + Triangulum, 14°, GALAXY
  - M81/M82 + IFN, 4°, OTHER
  - Markarian's Chain / Virgo, 6°, GALAXY
  - Eagle / Swan / M24 (Scutum–Sagittarius), 8°, EMISSION
  - Lagoon + Trifid, 5°, EMISSION
  - Rho Ophiuchi, 8°, REFLECTION
  - Milky Way core, 20°, OTHER
- `candidates.build_pool_parts` appends these as `Candidate(catalog="WF", prior=0.7, aliases=frozenset())`, with
  `CATALOG_PRIOR["WF"] = 0.7`. They take **no** history folding (member keys keep their own history). The
  size window keeps them off narrow rigs automatically. The candidate diversity step already stops them
  crowding out their member objects.
- `loader.resolve_target_key` already accepts any key in `pool.index`, so pin, snooze and dismiss work.
- **Frontend** `frontend/src/pages/Tonight.jsx` `PickCard`: when `catalog === "WF"`, replace "Open target"
  with member links (`members` is added to `pick_to_dict` for WF picks), because `/targets/WF_…` has no
  images. Any other place that links a pick to `/targets/` uses the same guard.

## 4. Tonight page (rig plan)

`frontend/src/pages/Tonight.jsx` `RigPlanSection` (~l.504):
- Under each rig name, show a verdict badge (`entry.verdict.level`) and the size window
  (e.g. "targets 3°–16°").
- An empty column shows `entry.note` instead of the generic "Nothing feasible for this rig tonight."
- Style additions go in `Tonight.css`, using the existing verdict colours.

## 5. Tests (`backend/tests/`)

- `test_recommend_scoring.py`:
  - The window defaults from the FOV, and declared values override them.
  - M92-like (14′) on a 6D-like rig is TOO_SMALL.
  - A 6° target passes, and a 25° target is TOO_BIG.
  - The exemption works by rig id and by scale within ±25%, and not for a scale that differs.
- `test_recommend_candidates.py`: WF entries join the pool, have unique keys, and fold no history.
- `test_recommendations_api.py` / render tests:
  - The rig plan leaves out pairs under 1.5 h.
  - An empty rig carries a `note` naming the main reason.
  - `verdict` is present, and `PAYLOAD_FORMAT` is 4.
- The Equipment API round-trips `min_target_arcmin` and `max_target_arcmin`.
- Update any existing tests that assume `MAX_FILL_RATIO` or the forced primary.

## 6. Verification

1. Run the backend tests on the host with the dummy env vars from 20260926-HANDOVER-recommendations §5. The known
   pre-existing failure in `test_reindex_backfill` is expected.
2. Run `npm run build` and `npm run lint` in `frontend/`.
3. **Replay check** (inside the backend container), before and after:
   `python -m app.scripts.replay_recommendations --since 2024-01-01`. Compare hit@k, MRR and
   `feasible_recall`. Any new TOO_SMALL/TOO_BIG misses are listed, so confirm they're sensible, and report the
   numbers to the user.
4. Bump `VERSION` from `20260930.06` to `20260930.07`. Then run
   `docker compose build backend frontend && docker compose up -d backend frontend`.
   The migration runs on start.
5. Check the API for tonight (`GET /api/recommendations`, which is not cached after the format bump):
   - The 6D column has no M92. It has a wide-field region, or a "nothing good" note explaining the Moon.
   - North America stays on the ZS73R.
   - The C11 picks are unchanged apart from sub-3.5′ targets such as NGC7026.
6. The user does the browser check (Equipment size inputs and the Tonight rig columns).
7. Update the docs:
   - `docs/features/RECOMMENDATIONS.md`: the size window, exemption, WF candidates, the rig-plan quality bar,
     `rig_summary`/`note`/`verdict`.
   - `docs/features/EQUIPMENT.md`: the size fields.
   - `docs/core/DATABASE_SCHEMA.md`: the new columns.
