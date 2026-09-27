# R2a: Recommendation Feedback, Outcomes and Dashboard Tile

Status: **Proposed** · Written: 2026-09-27 · Base: `main` @ `24c5e08` · Alembic head: `f9b1d5a62009`
· Data migrations applied: `0001`–`0008` · VERSION `20260927.01`

Read first:
- [HANDOVER-recommendations.md](HANDOVER-recommendations.md)
- [R1-recommendation-engine.md](R1-recommendation-engine.md): §4 engine, §7 API contract, §14 and
  §14.1 implementation notes
- [R1-replay-results.md](R1-replay-results.md): why feedback now matters more than replay
- [README.md](README.md) §3–§5

This is the **first slice of R2** (owner decision, 2026-09-27). R2b comes later: the affinity,
novelty and revisit lanes, inferred goals, and palette completeness. R2b will be tuned against the
feedback and outcomes this slice collects.

## 1. Problem

The R1 replay can only measure habit: "would it have predicted what you did". The owner kept the
spec's default weights rather than tune for habit (R1-replay-results §5). So there's currently no
way to tell whether the Tonight page gives **good advice**, and no way for the owner to steer it.
They can't say "stop showing me M42", "I want NGC7000 at the top this month", or "not this week".

## 2. Goals / non-goals

**Goals**
1. **Feedback actions** on every Tonight pick: Pin, Snooze (1 night / 1 week / 1 month), Not
   interested (with an optional reason), "I imaged it", and Undo. They're stored per user and
   applied immediately.
2. **The engine respects them.** Snoozed and dismissed targets leave the lanes. Pinned targets get
   their own lane at the top, and pins that can't be imaged tonight say why.
3. **Impressions and outcomes.** Log what Tonight showed for the current night, then measure
   afterwards whether the owner imaged it. This **acted-on rate** is the advice-quality metric that
   replaces replay as the thing to watch.
4. **A Dashboard tile** showing tonight's verdict and top pick, linking to Tonight.
5. **Hidden-items manager** and a **pin toggle on TargetDetail**.

**Non-goals**
- Learning weights from feedback automatically (R2b, once there's data).
- The R2b lanes, weather and season planning (R3), and notifications.

## 3. Data model

### 3.1 Migration: Alembic **`a0c2e6b73010`** (`down_revision = 'f9b1d5a62009'`, defensive)

```python
class RecommendationTargetState(Base):     # "recommendation_target_state": current state, 1 row per (user, key)
    user_id     = FK users.id ON DELETE CASCADE, part of PK
    target_key  = String(64), part of PK            # canonical key (candidate pool key)
    pinned      = Boolean, default False, not null
    snoozed_until = Date, null                      # hidden for nights < snoozed_until
    dismissed   = Boolean, default False, not null
    dismiss_reason = String(20), null               # DONE | NOT_MY_TYPE | TOO_HARD | OTHER
    note        = String(200), null
    updated_at  = DateTime, not null, index

class RecommendationEvent(Base):           # "recommendation_events": append-only log of every action
    id, user_id FK (CASCADE), target_key String(64) index
    action      = String(12)   # PIN | UNPIN | SNOOZE | UNSNOOZE | DISMISS | UNDISMISS | IMAGED
    night       = Date, null   # the night being viewed when the action was taken
    lane        = String(20), null; rank Integer null; score Float null; rig_id Integer null
    payload     = JSONB, null  # e.g. {"nights": 7, "reason": "NOT_MY_TYPE"}
    created_at  = DateTime, not null, index

class RecommendationImpression(Base):      # "recommendation_impressions"
    id, user_id FK (CASCADE), night Date, target_key String(64)
    site_id Integer null, rig_mode String(16), rig_id Integer null
    lane String(20), rank Integer, score Float, is_hero Boolean
    first_shown_at DateTime
    UNIQUE (user_id, night, target_key)            # first showing wins; later views don't overwrite
```

- The **state** table is what the engine reads, and the **event** log is for history and analysis.
  Every state change writes exactly one event, in the same transaction.
- **Snooze semantics:** a target is hidden for night N if `snoozed_until > N`. "1 night" sets
  `snoozed_until = viewed night + 1`, "1 week" `+ 7`, "1 month" `+ 30`. Undo clears it.
- **Dismiss has no expiry.** It's undone from the hidden-items manager.
- **IMAGED** only writes an event (with `night`). The imaging data itself arrives through the
  indexer. It records what the owner *says* they acted on, which can be compared with what the
  library shows.
- Feedback is keyed on the **canonical target key**, so a pin on "NGC3031" is stored as `M81`.
  Resolve through the alias index, like `GET /api/recommendations/target/{key}`.

## 4. Engine integration (pure, in `services/recommend/`)

- **New `feedback.py`:**
  - `FeedbackState(pinned: frozenset, snoozed: Mapping[key, date], dismissed: Mapping[key, reason])`
  - `apply_feedback(ranked, excluded, feedback, night) -> (ranked', hidden_counts, pinned_unavailable)`
- **Apply feedback after scoring, so the cache stays shared:**
  - The cached result stays **user-independent**: `recs:result:*` still holds the full `ranked`
    list and the context.
  - On each request, the loader loads that user's `FeedbackState` (one small query, no cache
    needed), then applies `apply_feedback`.
  - It then **rebuilds lanes and the hero** with the existing `build_lanes` / `verdict`.
  - So feedback never invalidates or fragments the result cache, and the 12:00 UTC pre-compute
    stays valid for every user. This needs `Result.ranked` to hold every feasible pick, not just
    the lane-sized ones. Check that it does; if not, cache the full list.
- **Hidden:** snoozed targets (for this night) and dismissed targets are removed from `ranked`.
  They're counted in `excluded_counts` as `SNOOZED` / `DISMISSED`, which never counts as
  infeasible. The per-target explain endpoint reports them as
  `excluded_reason: "SNOOZED" | "DISMISSED"` with `details.until` / `details.reason`.
- **Pinned lane:** a new lane, `pinned` ("Your pins"), comes **first** in lane order, and is
  excluded from diversity reordering.
  - It holds every feasible pinned pick, by score. A pinned target never also appears in another
    lane.
  - Pins that aren't feasible tonight go in `pinned_unavailable:
    [{target_key, name, excluded_reason}]`, so the owner can see why.
- **Hero:** still the highest-scoring pick, but ties within 0.02 go to a pinned pick. There's no
  score boost otherwise: pins are about visibility, not about distorting the ranking.
- **Replay** passes an empty `FeedbackState`, so its results are unchanged.

## 5. Impressions and outcomes

- **Logging:**
  - `GET /api/recommendations` inserts impressions only when **the night being viewed is tonight's
    default night**, so browsing other dates doesn't count.
  - It logs the hero plus the first `per_lane` items of each lane, with lane, rank (1-based, overall
    display order) and score.
  - Use `INSERT … ON CONFLICT DO NOTHING`, so the first showing is kept.
  - Write it off the request path (FastAPI `BackgroundTasks`) so cached responses stay under
    200 ms.
- **Outcome definition:** an impression (user, night N, key) is **acted on** if any light sub of
  that key (including keys folded into it, R1 §14.1) has `night_of(...) == N`. Use the same SQL
  night expression as Targets. Nights after `today − 1` are "pending".
- **`GET /api/recommendations/outcomes?days=90`** (any authenticated user, own data):

```jsonc
{
  "since": "2026-06-29", "nights_with_impressions": 14, "nights_imaged": 6,
  "hero":  {"shown": 14, "acted": 3, "rate": 0.21},
  "any":   {"shown": 280, "acted": 9, "rate": 0.032, "nights_with_any_acted": 5},
  "by_lane": {"pinned": {"shown": 10, "acted": 4, "rate": 0.4}, "active": {...}, ...},
  "imaged_not_shown": 3,        // nights where the owner imaged something the page never showed
  "self_reported": {"imaged_events": 4, "confirmed_by_library": 3},
  "pending_nights": 1
}
```

  **`nights_imaged` is the denominator that matters.** On nights with no imaging at all (cloud),
  nothing could have been acted on, so the rates use only nights on which the owner imaged
  something.

## 6. API changes (router `api/recommendations.py`)

All endpoints need an authenticated user. Feedback is the user's own, so it isn't admin-only.

| Method & path | Purpose |
|---|---|
| `POST /api/recommendations/feedback` | Body: `{target_key, action, nights?, reason?, note?, context?: {night, lane, rank, score, rig_id}}`. `action` ∈ PIN, UNPIN, SNOOZE (needs `nights` ∈ {1, 7, 30}), UNSNOOZE, DISMISS (optional `reason`), UNDISMISS, IMAGED. Returns the key's state. 404 for an unknown key; 400 for a bad action or nights. |
| `GET /api/recommendations/feedback` | `{items: [{target_key, name, pinned, snoozed_until, dismissed, dismiss_reason, note, updated_at}]}`, listing only keys with some active state. |
| `GET /api/recommendations/outcomes?days=90` | §5 |

**`GET /api/recommendations` response (additive only):**
- the lanes may include `{"id": "pinned", "title": "Your pins", …}` first;
- each `Pick` gets `"feedback": {"pinned": bool, "snoozed_until": str|null}`;
- `excluded_counts` gains `SNOOZED` and `DISMISSED`;
- there's a new top-level `"pinned_unavailable": [{target_key, name, excluded_reason}]`;
- `context` gains `"feedback_counts": {"pinned": n, "snoozed": n, "dismissed": n}`.

**`GET /api/recommendations/target/{key}`** gains a top-level
`"feedback": {pinned, snoozed_until, dismissed, dismiss_reason}`.

**Unchanged:** the `recs:result:*` cache (§4). Feedback writes invalidate nothing. The loader's
per-request feedback query is indexed on the `user_id` PK prefix.

## 7. Frontend

- **Tonight cards and hero:** add an action row with icon buttons and tooltips:
  - **Pin/Unpin** (lucide `Pin`/`PinOff`; if missing in 0.292, inline an SVG as in `TelescopeIcon.jsx`)
  - **Snooze ▾** (1 night / 1 week / 1 month)
  - **Not interested ▾** (Done with it / Not my kind of target / Too hard / Other)
  - **"I imaged it"**

  Updates are optimistic: the card leaves or moves lanes at once, and a toast shows
  "Snoozed M42 for a week · Undo". Undo sends the inverse action. Send `context` (night, lane,
  rank, score, rig_id) with every action.
- **Pinned lane** renders first, with the same cards. `pinned_unavailable` renders as a muted
  strip under it: "Pinned but not tonight: NGC7000 (below horizon), …".
- **Hidden-items manager:** a "Hidden (n)" link in the context strip opens a modal listing snoozed
  (with until-date) and dismissed (with reason) targets, each with Restore.
- **Outcomes panel:** "Advice outcomes (last 90 days)" is a collapsible panel for every user
  (it's their own data), next to the admin replay panel. It shows:
  - hero acted-on rate;
  - any-pick acted-on rate;
  - a per-lane table;
  - "imaged but not shown";
  - self-reported vs confirmed.

  With fewer than 5 imaged nights it says "Collecting data: N imaged nights so far".
- **Dashboard tile** (`pages/Dashboard.jsx`, a new `dashboard-card` near the top of the grid):
  - title "Tonight", the verdict pill, and the hero name + rig + mode with 2 reason chips;
  - the dark window (site local time), Moon %, and a "No rig mounted" hint for `ALL_FALLBACK`;
  - "Open Tonight →".
  - It calls `fetchRecommendations({perLane: 1})` via React Query (staleTime 10 min).
  - It hides itself on a 404 (no site) and shows a one-line "Set up a site in Equipment" link
    instead.
- **TargetDetail:** a "Pin for Tonight" toggle in the header, using the feedback endpoints.
- **Client:** the new functions go in the existing `// Recommendations (R1)` section of
  `client.js`, **appended at its end**, under a `// R2a` sub-comment.

## 8. Files

**New:**
- `backend/app/models/recommendation.py`, `backend/app/services/recommend/feedback.py`
- `backend/app/services/recommend/outcomes.py` (pure computation) + the SQL in `loader.py`
- `backend/alembic/versions/2026_09_27_1300-a0c2e6b73010_recommendation_feedback.py`
- Tests: `test_recommend_feedback.py`, `test_recommend_outcomes.py`, and extend
  `test_recommendations_api.py`

**Modified:**
- `models/__init__.py`, `services/recommend/{__init__,lanes,loader}.py`
- `api/recommendations.py`, `schemas/recommendations.py`
- `docs/features/RECOMMENDATIONS.md`, `docs/core/DATABASE_SCHEMA.md`
- `frontend/src/pages/Tonight.jsx` (+ `.css`), `Dashboard.jsx` (+ `.css`), `TargetDetail.jsx`,
  `api/client.js`

## 9. Tests

- **`test_recommend_feedback.py`:**
  - snooze hides for nights < until and shows on the until-night;
  - dismiss hides on every night;
  - a pinned feasible pick lands in `pinned` first and appears in no other lane;
  - a pinned infeasible pick goes in `pinned_unavailable` with its reason;
  - the hero tie-break (within 0.02) prefers a pin;
  - an empty state gives a result identical to R1;
  - lane rebuild after filtering keeps the per-lane caps and diversity rules.
- **`test_recommend_outcomes.py`:**
  - acted-on detection using `night_of`, with folded keys counted;
  - the denominator is imaged nights only;
  - pending nights are excluded;
  - `imaged_not_shown`;
  - self-reported vs confirmed.
- **API:**
  - every action and its inverse;
  - one event per state change;
  - alias → canonical key (NGC3031 → M81);
  - 404 and 400 cases;
  - feedback from user A doesn't affect user B;
  - impressions are written only for the default night and never duplicated;
  - a cached GET with feedback applied still sets `cached: true`.
- **Frontend:** `npm run build` and `npm run lint`, with no new problems (baseline 36).

## 10. Acceptance criteria (live, after deploy)

1. Pin NGC7000: it appears in "Your pins" at the top of Tonight and in no other lane. Unpin
   restores the R1 layout.
2. Snooze M42 for a week: it's absent tonight and for the next 6 nights (checked with the date
   picker) and back on night 7. The explain endpoint says `SNOOZED` with the until-date.
3. Dismiss a target: it's gone on every date, listed in Hidden with its reason, and Restore brings
   it back.
4. Loading Tonight writes impressions exactly once per (user, night, key). Browsing other dates
   writes none. `/outcomes` returns the §5 shape; with no past impressions it shows the
   "collecting data" state.
5. The Dashboard tile's verdict and hero match Tonight's for the same night and rig mode.
6. With feedback in place, a warm Tonight response still takes under 200 ms, and the 12:00 UTC
   pre-compute still produces a cache hit.

## 11. Execution plan

This is the same pattern as R0/R1: agents in worktrees with no Docker, live DB or `main` access,
and no pushes.

| Step | Who | Scope | Estimate |
|---|---|---|---|
| B1 | agent (Opus), `feat/r2a-feedback-backend` | §3–§6, §8–§9 backend | 180–250k |
| B2 | agent (Sonnet), `feat/r2a-feedback-frontend` | §7, against the §5/§6 contract, in parallel | 120–180k |
| Deploy | orchestrator | Trial merge, tests/build/lint, backup, merge, VERSION, rebuild both, check §10 in the browser with the owner | 60–90k |
| **Total** | | | **~0.35–0.5M** |

**Merge hotspots:**
- `client.js` (the R1 section's end);
- `Tonight.jsx`, which is only touched by B2;
- `loader.py` and `api/recommendations.py`, which are only touched by B1.

## 12. Decisions (defaults; change before B1 starts if needed)

1. **Feedback is per user**; the engine result cache is shared (§4).
2. **Pins don't boost scores.** They only guarantee visibility and win ties within 0.02.
3. **Snooze lengths are fixed** at 1 / 7 / 30 nights. Dismiss is indefinite until restored.
4. **Impressions are logged only for tonight's default view.** Browsing other dates is planning,
   not advice.
5. **The outcome rates count only nights with imaging**, so cloudy nights don't dilute them.
