# R1 Replay Results

Written: 2026-09-27 · Engine: `main` @ `410d601` (VERSION `20260927.01`) · Spec: [R1-recommendation-engine.md](R1-recommendation-engine.md) §5, §11 item 2

The replay runs the engine "as of" each past imaging night, using only history from before that
night. It then checks whether the targets actually imaged that night appear near the top. The
runs were done inside the backend container against the live library, with
`python -m app.scripts.replay_recommendations [--grid --grid-moon]`.

## 1. Test set

- **272 nights** have ≥ 30 min of resolved light subs, out of 351 nights with any resolved target.
- **473 actual (night, target) pairs:**
  - by site: 254 HOME (within 1.5° of a configured site), 193 UNKNOWN_SITE (no coordinates),
    26 REMOTE;
  - by target source: 220 header-named, 253 from plate-solve matching (`MATCH`).
- **Headline = HOME + UNKNOWN_SITE**, which covers 252 nights. REMOTE pairs are hosted-telescope
  data from other latitudes. They are reported separately, because the engine plans for the
  home site only.
- **Pool coverage is 96.8%.** 14 stray keys (mosaic panel suffixes such as `OBJ:NGC22441`, and
  `OBJ:M81LUM`) are folded into their parent targets, for history only.

## 2. Round 1 (first deploy, `20260926.16`): failed

Default weights with the spec's hard filters:

| | hit@1 | hit@5 | hit@10 | MRR | feasible recall |
|---|---|---|---|---|---|
| engine | 0.096 | 0.268 | 0.357 | 0.179 | **0.667** |
| recency baseline | 0.331 | 0.493 | 0.540 | 0.407 | 0.667 |

The causes, taken from the per-miss details:
- **Moon:** the broadband rule wanted about 115° of separation; the owner shoots LRGB/OSC at
  25–80°.
- **Horizon:** Orion peaks at about 28–30° from ~56°N, below the learned south bins.
- **Bright nights:** broadband was dropped in the BRIGHT tier.
- **Pixel size:** the 40 px minimum dropped real targets (M52 on the 20.76″ rig, IC10).
- **Test set:** remote-telescope nights were scored against home.
- **Candidate pool:** imaged IC/NGC objects with OpenNGC type `*`, `Other` or `Dup` were missing.

## 3. Round 2 (tuning, `20260927.01`)

The hard filters became generous: they drop only clear-cut cases, and the full rules still drive
the score (spec §14.1).

**Default weights:**

| | hit@1 | hit@3 | hit@5 | hit@10 | MRR | feasible recall |
|---|---|---|---|---|---|---|
| engine | 0.103 | 0.214 | 0.290 | 0.389 | 0.198 | **0.910** ✅ |
| recency | 0.409 | 0.552 | 0.623 | 0.675 | 0.502 | 0.910 |
| altitude | 0.000 | 0.032 | 0.040 | 0.071 | 0.030 | 0.910 |
| random | 0.001 | 0.006 | 0.011 | 0.021 | 0.013 | 0.910 |

The remaining headline misses are MOON 21, TOO_SMALL 15 (all incidental `MATCH` galaxies),
NOT_IN_POOL 15, TOO_BIG 2 and BELOW_HORIZON 1. REMOTE pairs score hit@5 0.04 with feasible recall
0.58, as expected, since they're scored from the wrong location.

**Grid search** covered 288 weight combinations, after a Moon stage over the broadband D value.

- **Moon stage:** BB D=60 scored best at hit@5 0.318, ahead of D=90 (0.282) and D=120 (0.290).
- **Top weight settings** (the recency baseline on the same pairs scores hit@5 0.627, MRR 0.505):

| # | hit@5 | MRR | tau | observability | framing | project | momentum | urgency | prior | recency_rank |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.643 | 0.521 | 45 | 0.125 | 0.1 | 0.0 | 0.3 | 0.075 | 0.05 | 0.2 |
| 2 | 0.643 | 0.521 | 20 | 0.125 | 0.1 | 0.0 | 0.6 | 0.075 | 0.05 | 0.2 |
| 3 | 0.639 | **0.523** | 45 | 0.125 | 0.2 | 0.0 | 0.45 | 0.075 | 0.1 | 0.2 |

## 4. Reading the results

- **Feasibility is fixed.** 91% of what the owner actually imaged passes the hard filters, up from
  67%.
- **The tuned engine beats every baseline, but only just**: +1.6 points on hit@5 and +1.8 on
  MRR over recency. With about 250 nights, the standard error on hit@5 is about ±3 points, so
  "at least as good as recency" is the honest reading, not "clearly better".
- **Replay rewards habit.** The best settings drop `project` to 0 and put most of the weight on
  recency (`momentum` + `recency_rank`). That's the limitation research §5 warned about: the metric
  measures "would it have predicted what you did", not "was it good advice". The engine's added
  value is:
  - the feasibility filtering, which recency alone doesn't have;
  - lanes and reasons;
  - Moon and tier handling on nights the owner hasn't imaged before;
  - the "Continue a project" and "Last chance" lanes, which use the components whatever their
    weights.
- **The default-weight engine loses badly on header-named targets** (hit@5 0.18 vs 0.59). Those
  are the owner's deliberate choices, so this is where recency weighting matters most.

## 5. Decision (owner, 2026-09-27)

**Keep the spec defaults.** That means the weights `observability 0.25, framing 0.20, project 0.20,
momentum 0.15, urgency 0.15, prior 0.10, recency_rank 0`, tau 45 days, and broadband Moon D=120.
The tuned setting (#3 above) was offered and declined. It only matched recency within noise, and
it did so by dropping project value entirely.

What follows:
- **§11 item 2 is knowingly not met.** Feasible recall passes (0.91), but the engine doesn't beat
  the recency baseline on hit@5 or MRR (0.29 vs 0.62). The replay is kept as a diagnostic, not a
  gate.
- The tunable parameters stay in place (`tau`, `recency_rank`, `--moon-bb`, `--grid`), so the
  decision can be revisited cheaply. The one-line change is the `Weights` defaults in
  `services/recommend/scoring.py`.
- Owner feedback on the Tonight page is the better judge of advice quality, and R2's feedback
  table will capture it.

## 6. Follow-ups (not blockers)

- REMOTE sites have no ephemeris of their own. Accepting them as sites (Equipment > detect with
  "include older") would let the replay score them correctly.
- The 15 `MATCH` "too small" misses are incidental galaxies in wide fields. A target-resolution
  rule that prefers the largest matched object would clean up both Targets and the replay.
- Seven imaged objects remain outside the pool (15 pairs).
