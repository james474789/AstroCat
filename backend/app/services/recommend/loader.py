"""
DB -> engine inputs, caching and the request-level service (R1 spec §6). Not pure.

Uses a sync session (Celery, scripts, and the API through run_in_threadpool).

Caches:
- in process: the candidate pool (rebuilt when the alias index is rebuilt or
  the set of imaged keys changes) and the history inputs per hist_version;
- Redis `recs:inputs:v<hist_version>` (history rows, masters, goals; 1 h);
- Redis `recs:result:v2:<site>:<rig>:<night>:<hist_version>` (6 h): the
  user-independent engine payload (every feasible pick; R2a §4). It no longer
  depends on per_lane: lanes are rebuilt per request.
hist_version = max(images.updated_at) + count of light subs + target_goals
count/max(updated_at). Equipment/site/mount/horizon writes and every
assign_equipment run delete `recs:*`.

R2a (docs/design/R2a-feedback-dashboard.md): each request loads the user's
FeedbackState (one indexed query) and renders the cached payload with it, so
feedback writes invalidate nothing. Feedback writes, impressions and the
outcomes queries are here too.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select, text

from app.services.recommend import (
    PAYLOAD_FORMAT, RIG_MODE_ALL, RIG_MODE_ALL_FALLBACK, RIG_MODE_MOUNTED, RIG_MODE_SINGLE, EngineInputs, Params,
    explain_target, recommend, render_payload, result_payload,
)
from app.services.recommend.feedback import (
    DISMISS, EXCL_SNOOZED, IMAGED, FeedbackError, FeedbackState, TargetState, event_payload,
    transition, validate_action,
)
from app.services.recommend.outcomes import ImagedRow, Impression, compute_outcomes, imaged_by_night
from app.services.recommend.candidates import CandidatePool, build_pool_parts, fold_map
from app.services.recommend.context import HorizonSpec, SiteSpec, resolve_horizon
from app.services.recommend.history import (
    CLASS_BB, CLASS_OSC, GoalRow, HistoryRow, band_classes, build_history, filter_classes, infer_goals,
    narrowband_keys, remap_rows, sort_rows,
)
from app.services.recommend.scoring import RigSpec
from app.utils.filter_names import normalize_filter
from app.utils.observing_night import NIGHT_JOIN_SQL, NIGHT_SQL
from app.utils.optics import fov_deg, pixel_scale

logger = logging.getLogger(__name__)

INPUTS_CACHE_KEY = "recs:inputs:v{version}"
INPUTS_CACHE_TTL = 3600
RESULT_CACHE_KEY = "recs:result:v2:{site}:{rig}:{night}:{version}"
RESULT_CACHE_TTL = 6 * 3600

_VALID_SCALE = "images.pixel_scale_arcsec BETWEEN 0.05 AND 300 AND abs(images.pixel_scale_arcsec - 72.0) >= 0.001"

HISTORY_SQL = text(f"""
    SELECT images.target_key AS key, {NIGHT_SQL} AS night, images.filter_name AS filter_name,
           c.is_color AS is_color, images.rig_id AS rig_id, images.site_id AS site_id,
           sum(images.exposure_time_seconds) AS seconds,
           min(CASE WHEN {_VALID_SCALE} THEN images.pixel_scale_arcsec END) AS best_scale
    FROM images {NIGHT_JOIN_SQL}
    LEFT JOIN rigs r ON r.id = images.rig_id
    LEFT JOIN cameras c ON c.id = r.camera_id
    WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
      AND images.target_key IS NOT NULL AND images.target_source IS DISTINCT FROM 'NONE'
      AND images.exposure_time_seconds > 0 AND images.capture_date IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6
""")

MASTERS_SQL = text(f"""
    SELECT images.target_key AS key, min({NIGHT_SQL}) AS first_night
    FROM images {NIGHT_JOIN_SQL}
    WHERE images.frame_type = 'LIGHT' AND images.subtype = 'INTEGRATION_MASTER'
      AND images.target_key IS NOT NULL AND images.capture_date IS NOT NULL
    GROUP BY 1
""")

# Replay only: per (target, night, rounded latitude, target_source) seconds, to
# classify actual pairs as HOME / REMOTE / UNKNOWN_SITE and header vs MATCH.
PAIRS_SQL = text(f"""
    SELECT images.target_key AS key, {NIGHT_SQL} AS night,
           round(CAST(images.site_latitude AS numeric), 1) AS lat, images.target_source AS source,
           sum(images.exposure_time_seconds) AS seconds
    FROM images {NIGHT_JOIN_SQL}
    WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
      AND images.target_key IS NOT NULL AND images.target_source IS DISTINCT FROM 'NONE'
      AND images.exposure_time_seconds > 0 AND images.capture_date IS NOT NULL
    GROUP BY 1, 2, 3, 4
""")

GOALS_SQL = text("SELECT target_key, filter_group, goal_seconds, created_at FROM target_goals")

VERSION_SQL = text("""
    SELECT (SELECT max(updated_at) FROM images WHERE frame_type = 'LIGHT' AND subtype = 'SUB_FRAME') AS img_max,
           (SELECT count(*) FROM images WHERE frame_type = 'LIGHT' AND subtype = 'SUB_FRAME') AS img_count,
           (SELECT count(*) FROM target_goals) AS goal_count,
           (SELECT max(coalesce(updated_at, created_at)) FROM target_goals) AS goal_max
""")


REPLAY_LATEST_FILE = "replay_latest.json"


def replay_latest_path():
    """<log_dir>/replay_latest.json: written by the replay script, served by the API."""
    from pathlib import Path
    from app.config import settings
    return Path(settings.log_dir) / REPLAY_LATEST_FILE


class RecommendationError(Exception):
    """A request the API maps to an HTTP error (status + detail)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

def _redis():
    try:
        from app.services.data_migrations import redis_client
        return redis_client()
    except Exception:
        return None


def _cache_get(r, key: str):
    if r is None:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"Recommendations cache read failed: {e}")
        return None


def _cache_set(r, key: str, value, ttl: int) -> None:
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning(f"Recommendations cache write failed: {e}")


def invalidate_recommendations_cache(r=None) -> int:
    """Delete every recs:* key. Returns the number deleted."""
    r = r or _redis()
    if r is None:
        return 0
    try:
        keys = list(r.scan_iter(match="recs:*"))
        if keys:
            r.delete(*keys)
        return len(keys)
    except Exception as e:
        logger.warning(f"Recommendations cache invalidation failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# History inputs
# ---------------------------------------------------------------------------

@dataclass
class HistoryInputs:
    version: str
    rows: List[HistoryRow]                 # sorted by night
    masters: Dict[str, date]
    goal_rows: List[GoalRow]

    @property
    def imaged_keys(self) -> frozenset:
        return frozenset(r.key for r in self.rows)


_history_memo: Dict[str, HistoryInputs] = {}


def hist_version(session) -> str:
    row = session.execute(VERSION_SQL).mappings().first()
    if row is None:
        return "0"
    parts = [row["img_max"].isoformat() if row["img_max"] else "-", str(row["img_count"] or 0),
             str(row["goal_count"] or 0), row["goal_max"].isoformat() if row["goal_max"] else "-"]
    return "|".join(parts).replace(" ", "T").replace(":", "")


def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _as_datetime(v) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _serialise_history(h: HistoryInputs) -> Dict[str, Any]:
    return {
        "version": h.version,
        "rows": [[r.key, r.night.isoformat(), r.filter_name, r.seconds, r.rig_id, r.is_color, r.site_id, r.best_scale]
                 for r in h.rows],
        "masters": {k: v.isoformat() for k, v in h.masters.items() if v},
        "goals": [[g.key, g.filter_group, g.goal_seconds, g.created_at.isoformat() if g.created_at else None]
                  for g in h.goal_rows],
    }


def _deserialise_history(d: Dict[str, Any]) -> HistoryInputs:
    rows = [HistoryRow(k, _as_date(n), f, s, rid, col, sid, sc) for k, n, f, s, rid, col, sid, sc in d["rows"]]
    return HistoryInputs(
        version=d["version"], rows=sort_rows(rows),
        masters={k: _as_date(v) for k, v in d["masters"].items()},
        goal_rows=[GoalRow(k, fg, gs, _as_datetime(c)) for k, fg, gs, c in d["goals"]],
    )


def query_history_inputs(session, version: str = "") -> HistoryInputs:
    rows = [HistoryRow(r["key"], _as_date(r["night"]), r["filter_name"], float(r["seconds"] or 0.0), r["rig_id"],
                       r["is_color"], r["site_id"], r["best_scale"])
            for r in session.execute(HISTORY_SQL).mappings().all()]
    masters = {r["key"]: _as_date(r["first_night"]) for r in session.execute(MASTERS_SQL).mappings().all()}
    goals = [GoalRow(r["target_key"], r["filter_group"], r["goal_seconds"], r["created_at"])
             for r in session.execute(GOALS_SQL).mappings().all()]
    return HistoryInputs(version=version, rows=sort_rows(rows), masters=masters, goal_rows=goals)


def load_history_inputs(session, version: Optional[str] = None, r=None) -> HistoryInputs:
    version = version or hist_version(session)
    hit = _history_memo.get(version)
    if hit is not None:
        return hit
    cached = _cache_get(r, INPUTS_CACHE_KEY.format(version=version))
    if cached:
        try:
            inputs = _deserialise_history(cached)
        except Exception as e:
            logger.warning(f"Recommendations inputs cache unreadable: {e}")
            inputs = None
    else:
        inputs = None
    if inputs is None:
        inputs = query_history_inputs(session, version)
        _cache_set(r, INPUTS_CACHE_KEY.format(version=version), _serialise_history(inputs), INPUTS_CACHE_TTL)
    _history_memo.clear()
    _history_memo[version] = inputs
    return inputs


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------

@dataclass
class PoolData:
    """The candidate pool plus history re-keyed onto it (stray keys folded; `images` is untouched)."""
    pool: CandidatePool
    rows: List[HistoryRow]
    masters: Dict[str, date]
    goal_rows: List[GoalRow]
    key_map: Dict[str, str]

    def fold_stats(self, raw_rows: Sequence[HistoryRow] = ()) -> Dict[str, Any]:
        folded = [r for r in raw_rows if r.key in self.key_map]
        return {"keys": len(self.key_map), "rows": len(folded),
                "hours": round(sum(float(r.seconds or 0) for r in folded) / 3600.0, 1),
                "map": dict(sorted(self.key_map.items()))}


_pool_memo: Dict[str, Any] = {"index_id": None, "version": None, "data": None}


def build_pool_data(messier, caldwell, ngc, sh2, index, hist: "HistoryInputs") -> PoolData:
    """Pure part of load_pool_data (catalog rows + alias index + history)."""
    imaged = hist.imaged_keys
    candidates, dup_map = build_pool_parts(messier, caldwell, ngc, sh2, index, imaged, narrowband_keys(hist.rows))
    pool = CandidatePool(candidates, dup_map)
    key_map = fold_map(imaged, pool.index, index.resolve, dup_map)
    rows = remap_rows(hist.rows, key_map)
    masters: Dict[str, date] = {}
    for k, v in hist.masters.items():
        k2 = key_map.get(k, k)
        if v is not None and (k2 not in masters or v < masters[k2]):
            masters[k2] = v
    goals = [g._replace(key=key_map.get(g.key, g.key)) for g in hist.goal_rows]
    return PoolData(pool=pool, rows=rows, masters=masters, goal_rows=goals, key_map=key_map)


def load_pool_data(session, hist: "HistoryInputs") -> PoolData:
    from app.models.catalog import CaldwellCatalog, MessierCatalog, NGCCatalog, Sh2Catalog
    from app.services.targets import get_alias_index_sync

    index = get_alias_index_sync(session)
    data = _pool_memo["data"]
    if data is not None and _pool_memo["index_id"] == id(index) and _pool_memo["version"] == hist.version \
            and hist.version:
        return data
    messier = session.execute(select(MessierCatalog)).scalars().all()
    ngc = session.execute(select(NGCCatalog)).scalars().all()
    caldwell = session.execute(select(CaldwellCatalog)).scalars().all()
    sh2 = session.execute(select(Sh2Catalog)).scalars().all()
    data = build_pool_data(messier, caldwell, ngc, sh2, index, hist)
    if data.key_map:
        logger.info(f"Recommendations: folded {len(data.key_map)} stray history keys into pool keys")
    _pool_memo.update(index_id=id(index), version=hist.version, data=data)
    return data


def load_pool(session, hist: "HistoryInputs") -> CandidatePool:
    return load_pool_data(session, hist).pool


# ---------------------------------------------------------------------------
# Sites and rigs
# ---------------------------------------------------------------------------

def load_sites(session) -> List[SiteSpec]:
    from app.models.equipment import Site

    rows = session.execute(select(Site).order_by(Site.is_default.desc(), Site.name)).scalars().all()
    return [SiteSpec(id=s.id, name=s.name, latitude=s.latitude, longitude=s.longitude, timezone=s.timezone,
                     is_default=bool(s.is_default), horizon=s.horizon, horizon_source=s.horizon_source) for s in rows]


def pick_site(sites: Sequence[SiteSpec], site_id: Optional[int]) -> SiteSpec:
    if not sites:
        raise RecommendationError(404, "No site configured. Add one in Equipment > Sites.")
    if site_id is None:
        return next((s for s in sites if s.is_default), sites[0])
    site = next((s for s in sites if s.id == site_id), None)
    if site is None:
        raise RecommendationError(404, "Site not found")
    return site


def site_horizon(session, site: SiteSpec, r=None) -> HorizonSpec:
    from app.services.site_horizon import learned_horizon_sync

    try:
        learned = learned_horizon_sync(session, site.id, site.latitude, site.longitude, r)
    except Exception as e:
        logger.warning(f"Learned horizon unavailable for site {site.id}: {e}")
        learned = {"points": [], "floor_deg": None}
    return resolve_horizon(site.horizon, learned.get("points"), learned.get("floor_deg"))


@dataclass
class RigRecord:
    id: int
    name: str
    is_active: bool
    is_mounted: bool
    spec: Optional[RigSpec]
    skip_reason: Optional[str]


def rig_spec(rig_id: int, name: str, *, pixel_um: Optional[float], width_px: Optional[int],
             height_px: Optional[int], focal_mm: Optional[float], modifier_factor: Optional[float] = 1.0,
             binning: Optional[int] = 1, measured_scale: Optional[float] = None, is_color: Optional[bool] = None,
             filter_bands: Sequence[str] = (), seen_classes: Sequence[str] = ()) -> Tuple[Optional[RigSpec], Optional[str]]:
    """
    RigSpec for the engine, or (None, reason). Scale: declared, else measured.
    Classes: from the rig's filters; none + colour camera -> OSC; none + mono ->
    the classes seen on its images, else BB.
    """
    binning = int(binning or 1)
    declared = pixel_scale(pixel_um, focal_mm, binning, modifier_factor)
    scale = declared or measured_scale
    if not scale:
        return None, "no pixel scale"
    unbinned = pixel_scale(pixel_um, focal_mm, 1, modifier_factor) if declared else scale / binning
    fov = fov_deg(width_px, height_px, unbinned)
    if fov is None:
        return None, "no sensor size (field of view unknown)"
    classes = set()
    for band in filter_bands:
        classes |= band_classes(band, is_color)
    if not classes:
        if is_color:
            classes = {CLASS_OSC}
        else:
            classes = set(seen_classes) or {CLASS_BB}
    return RigSpec(id=rig_id, name=name, scale_arcsec=float(scale), fov_w_deg=fov[0], fov_h_deg=fov[1],
                   classes=frozenset(classes), is_color=bool(is_color)), None


def load_rigs(session, history_rows: Sequence[HistoryRow] = ()) -> List[RigRecord]:
    from app.models.equipment import Rig

    seen: Dict[int, set] = {}
    for r in history_rows:
        if r.rig_id is not None:
            seen.setdefault(r.rig_id, set()).update(c for c, _ in filter_classes(normalize_filter(r.filter_name),
                                                                                 r.is_color))
    rigs = session.execute(select(Rig).order_by(Rig.name)).unique().scalars().all()
    out = []
    for rig in rigs:
        cam, opt = rig.camera, rig.optic
        spec, reason = rig_spec(
            rig.id, rig.name, pixel_um=cam.pixel_size_um if cam else None,
            width_px=cam.sensor_width_px if cam else None, height_px=cam.sensor_height_px if cam else None,
            focal_mm=opt.focal_length_mm if opt else None, modifier_factor=rig.modifier_factor,
            binning=rig.binning, measured_scale=rig.measured_scale_arcsec,
            is_color=cam.is_color if cam else None, filter_bands=[f.band for f in rig.filters],
            seen_classes=sorted(seen.get(rig.id, ())))
        out.append(RigRecord(id=rig.id, name=rig.name, is_active=bool(rig.is_active),
                             is_mounted=bool(rig.is_mounted), spec=spec, skip_reason=reason))
    return out


def select_rigs(records: Sequence[RigRecord], rig: str = "mounted",
                pool_ids: Optional[Sequence[int]] = None) -> Tuple[List[RigSpec], str, List[Dict[str, Any]]]:
    """
    (specs, rig_mode, skipped_rigs) for rig = "mounted" | "all" | "<id>".
    mounted without a mounted rig falls back to all active rigs (ALL_FALLBACK).
    `pool_ids` (replay) overrides "all" with an explicit rig id list.
    """
    rig = (rig or "mounted").strip().lower()
    if rig == "mounted":
        chosen = [r for r in records if r.is_mounted]
        mode = RIG_MODE_MOUNTED
        if not chosen:
            chosen = [r for r in records if r.is_active]
            mode = RIG_MODE_ALL_FALLBACK
    elif rig == "all":
        ids = set(pool_ids) if pool_ids is not None else None
        chosen = [r for r in records if (r.id in ids if ids is not None else r.is_active)]
        mode = RIG_MODE_ALL
    else:
        try:
            rid = int(rig)
        except ValueError:
            raise RecommendationError(400, "rig must be 'mounted', 'all' or a rig id")
        chosen = [r for r in records if r.id == rid]
        if not chosen:
            raise RecommendationError(404, "Rig not found")
        mode = RIG_MODE_SINGLE
    specs = [r.spec for r in chosen if r.spec is not None]
    skipped = [{"id": r.id, "name": r.name, "reason": r.skip_reason} for r in chosen if r.spec is None]
    return specs, mode, skipped


# ---------------------------------------------------------------------------
# Nights
# ---------------------------------------------------------------------------

def default_night(now_utc: datetime, lon: float) -> date:
    """
    Tonight at the site: the night that starts at the coming local solar noon
    once the morning has begun (06:00-12:00 local solar time), else the
    current observing night.
    """
    local_solar = now_utc + timedelta(hours=float(lon or 0.0) / 15.0)
    if 6 <= local_solar.hour < 12:
        return local_solar.date()
    return (local_solar - timedelta(hours=12)).date()


# ---------------------------------------------------------------------------
# Request-level service
# ---------------------------------------------------------------------------

def build_inputs(session, site: SiteSpec, night: date, rig: str, version: Optional[str] = None, r=None,
                 as_of: Optional[date] = None) -> Tuple[EngineInputs, str]:
    hist_inputs = load_history_inputs(session, version, r)
    data = load_pool_data(session, hist_inputs)
    pool = data.pool
    history = build_history(data.rows, as_of=as_of, masters=data.masters, presorted=True)
    goals = infer_goals(history, {c.key: c.kind for c in pool.candidates}, data.goal_rows, as_of)
    records = load_rigs(session, data.rows)
    specs, mode, skipped = select_rigs(records, rig)
    horizon = site_horizon(session, site, r)
    inputs = EngineInputs(candidates=pool, history=history, site=site, horizon=horizon, rigs=specs, goals=goals,
                          night=night, now=datetime.utcnow(), skipped_rigs=skipped)
    return inputs, mode


def resolve_target_key(session, key: str, pool: CandidatePool,
                       key_map: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """
    The canonical candidate key for a key, name or alias (alias index, Dup map
    and the R1 §14.1 stray-key folds), or None when it isn't a candidate.
    """
    if key in pool.index:
        return key
    from app.services.targets import get_alias_index_sync, normalize_designation

    alias = get_alias_index_sync(session).resolve(key)
    for k in (alias, normalize_designation(key)):
        if k and k in pool.index:
            return k
        if k and k in pool.dup_map:
            return pool.dup_map[k]
        if k and key_map and key_map.get(k) in pool.index:
            return key_map[k]
    return None


# ---------------------------------------------------------------------------
# Feedback state (R2a §3-§4)
# ---------------------------------------------------------------------------

def load_feedback_state(session, user_id: Optional[int]) -> FeedbackState:
    """The user's FeedbackState (one query on the (user_id, target_key) PK prefix); empty without a user."""
    if user_id is None:
        return FeedbackState.empty()
    from sqlalchemy import or_
    from app.models.recommendation import RecommendationTargetState as TS

    rows = session.execute(
        select(TS.target_key, TS.pinned, TS.snoozed_until, TS.dismissed, TS.dismiss_reason)
        .where(TS.user_id == user_id, or_(TS.pinned.is_(True), TS.dismissed.is_(True), TS.snoozed_until.isnot(None)))
    ).mappings().all()
    return FeedbackState.from_rows(rows)


def _hide_in_explanation(body: Dict[str, Any], feedback: FeedbackState, night: date) -> None:
    """R2a: a snoozed / dismissed target reports SNOOZED / DISMISSED for every rig."""
    key = body["target_key"]
    body["feedback"] = feedback.target_feedback(key)
    why = feedback.hidden_reason(key, night)
    if why is None:
        return
    for res in body["results"]:
        details = dict(res.get("details") or {})
        details["engine_excluded_reason"] = res.get("excluded_reason")
        if why == EXCL_SNOOZED:
            details["until"] = feedback.snoozed[key].isoformat()
        else:
            details["reason"] = feedback.dismissed.get(key)
        res["details"] = details
        res["excluded_reason"] = why
        res["pick"] = None


# ---------------------------------------------------------------------------
# GET /api/recommendations and /target/{key}
# ---------------------------------------------------------------------------

def _session_scope(session):
    if session is not None:
        from contextlib import nullcontext
        return nullcontext(session)
    from app.database import SessionLocal
    return SessionLocal()


def _engine_payload(s, site: SiteSpec, night: date, rig: str, r, use_cache: bool) -> Tuple[Dict[str, Any], bool]:
    """(user-independent payload, came from cache)."""
    version = hist_version(s)
    key = RESULT_CACHE_KEY.format(site=site.id, rig=(rig or "mounted").lower(), night=night.isoformat(),
                                  version=version)
    if use_cache:
        cached = _cache_get(r, key)
        if cached and cached.get("format") == PAYLOAD_FORMAT:
            return cached, True
    started = time.time()
    inputs, mode = build_inputs(s, site, night, rig, version, r)
    result = recommend(inputs, Params(rig_mode=mode))
    payload = result_payload(result, datetime.utcnow())
    logger.info(f"Recommendations for site {site.id} night {night} rig={rig}: "
                f"{len(result.ranked)} feasible in {time.time() - started:.2f}s")
    _cache_set(r, key, payload, RESULT_CACHE_TTL)
    return payload, False


def recommendations_view(night: Optional[date] = None, site_id: Optional[int] = None, rig: str = "mounted",
                         per_lane: int = 6, use_cache: bool = True, session=None,
                         user_id: Optional[int] = None) -> Tuple[Dict[str, Any], bool]:
    """
    (GET /api/recommendations body for this user, is tonight's default night).
    Raises RecommendationError for 4xx cases.
    """
    r = _redis()
    with _session_scope(session) as s:
        site = pick_site(load_sites(s), site_id)
        tonight = default_night(datetime.utcnow(), site.longitude)
        night = night or tonight
        payload, cached = _engine_payload(s, site, night, rig, r, use_cache)
        feedback = load_feedback_state(s, user_id)
    body = render_payload(payload, feedback, per_lane, cached=cached)
    return body, night == tonight


def get_recommendations(night: Optional[date] = None, site_id: Optional[int] = None, rig: str = "mounted",
                        per_lane: int = 6, use_cache: bool = True, session=None,
                        user_id: Optional[int] = None) -> Dict[str, Any]:
    """The §7 GET /api/recommendations body. Raises RecommendationError for 4xx cases."""
    return recommendations_view(night, site_id, rig, per_lane, use_cache, session, user_id)[0]


def get_target_explanation(key: str, night: Optional[date] = None, site_id: Optional[int] = None,
                           rig: str = "mounted", session=None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """The per-target "why / why not" body. 404 (RecommendationError) when the key isn't a candidate."""
    r = _redis()
    with _session_scope(session) as s:
        site = pick_site(load_sites(s), site_id)
        night = night or default_night(datetime.utcnow(), site.longitude)
        inputs, mode = build_inputs(s, site, night, rig, None, r)
        resolved = resolve_target_key(s, key, inputs.pool)
        if resolved is None:
            raise RecommendationError(404, f"'{key}' is not in the candidate pool")
        body = explain_target(inputs, Params(rig_mode=mode), resolved)
        feedback = load_feedback_state(s, user_id)
    _hide_in_explanation(body, feedback, night)
    body["rig_mode"] = mode
    body["skipped_rigs"] = inputs.skipped_rigs
    return body


# ---------------------------------------------------------------------------
# Feedback writes and listing (R2a §6)
# ---------------------------------------------------------------------------

def _dialect_insert(session, table):
    """INSERT with on_conflict_do_nothing for the session's dialect (PostgreSQL; SQLite in unit tests)."""
    name = session.get_bind().dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert(table)


def _state_item(key: str, name: str, row) -> Dict[str, Any]:
    return {
        "target_key": key, "name": name,
        "pinned": bool(row.pinned) if row is not None else False,
        "snoozed_until": row.snoozed_until.isoformat() if row is not None and row.snoozed_until else None,
        "dismissed": bool(row.dismissed) if row is not None else False,
        "dismiss_reason": row.dismiss_reason if row is not None else None,
        "note": row.note if row is not None else None,
        "updated_at": (row.updated_at.replace(microsecond=0).isoformat() + "Z")
        if row is not None and row.updated_at else None,
    }


def _pool_for_keys(s, r=None) -> "PoolData":
    return load_pool_data(s, load_history_inputs(s, None, r))


def _viewing_night(s, night: Optional[date]) -> date:
    if night is not None:
        return night
    try:
        site = pick_site(load_sites(s), None)
        return default_night(datetime.utcnow(), site.longitude)
    except RecommendationError:
        return default_night(datetime.utcnow(), 0.0)


def post_feedback(user_id: int, target_key: str, action: str, nights: Optional[int] = None,
                  reason: Optional[str] = None, note: Optional[str] = None,
                  context: Optional[Mapping[str, Any]] = None, session=None) -> Dict[str, Any]:
    """
    Apply one feedback action for the user; returns the key's state (a GET
    /feedback item). A state change writes the state and exactly one event in
    one transaction; IMAGED writes only an event; a no-op writes nothing.
    400 for a bad action / nights / reason / note, 404 for an unknown key.
    """
    from app.models.recommendation import RecommendationEvent, RecommendationTargetState as TS

    try:
        act = validate_action(action, nights, reason, note)
    except FeedbackError as e:
        raise RecommendationError(e.status, e.detail)
    ctx = dict(context or {})
    r = _redis()
    with _session_scope(session) as s:
        data = _pool_for_keys(s, r)
        key = resolve_target_key(s, (target_key or "").strip(), data.pool, data.key_map)
        if key is None:
            raise RecommendationError(404, f"'{target_key}' is not in the candidate pool")
        name = data.pool.get(key).name
        night = _viewing_night(s, _as_date(ctx.get("night")))
        try:
            row = None
            if act != IMAGED:
                s.execute(_dialect_insert(s, TS.__table__)
                          .values(user_id=user_id, target_key=key, pinned=False, dismissed=False,
                                  updated_at=datetime.utcnow())
                          .on_conflict_do_nothing(index_elements=["user_id", "target_key"]))
            row = s.execute(select(TS).where(TS.user_id == user_id, TS.target_key == key)
                            .with_for_update()).scalars().first()
            old = TargetState(pinned=bool(row.pinned), snoozed_until=row.snoozed_until, dismissed=bool(row.dismissed),
                              dismiss_reason=row.dismiss_reason, note=row.note) if row is not None else TargetState()
            new, changed = transition(old, act, night, nights, reason if act == DISMISS else None, note)
            if changed:
                row.pinned, row.snoozed_until, row.dismissed = new.pinned, new.snoozed_until, new.dismissed
                row.dismiss_reason, row.note = new.dismiss_reason, new.note
                row.updated_at = datetime.utcnow()
            if changed or act == IMAGED:
                s.add(RecommendationEvent(
                    user_id=user_id, target_key=key, action=act, night=night, lane=ctx.get("lane"),
                    rank=ctx.get("rank"), score=ctx.get("score"), rig_id=ctx.get("rig_id"),
                    payload=event_payload(act, nights, reason, note), created_at=datetime.utcnow()))
            s.commit()
        except Exception:
            s.rollback()
            raise
        item = _state_item(key, name, row)
    return item


def list_feedback(user_id: int, session=None, today: Optional[date] = None) -> Dict[str, Any]:
    """GET /api/recommendations/feedback: keys with an active state (pinned, dismissed or snoozed to >= today)."""
    from sqlalchemy import or_
    from app.models.recommendation import RecommendationTargetState as TS

    today = today or datetime.utcnow().date()
    r = _redis()
    with _session_scope(session) as s:
        rows = s.execute(
            select(TS).where(TS.user_id == user_id,
                             or_(TS.pinned.is_(True), TS.dismissed.is_(True), TS.snoozed_until >= today))
            .order_by(TS.updated_at.desc(), TS.target_key)
        ).scalars().all()
        pool = _pool_for_keys(s, r).pool if rows else None
        items = []
        for row in rows:
            cand = pool.get(row.target_key) if pool is not None else None
            item = _state_item(row.target_key, cand.name if cand else row.target_key, row)
            if row.snoozed_until is not None and row.snoozed_until < today:
                item["snoozed_until"] = None      # expired
            items.append(item)
    return {"items": items}


# ---------------------------------------------------------------------------
# Impressions and outcomes (R2a §5)
# ---------------------------------------------------------------------------

def record_impressions(user_id: int, rows: Sequence[Mapping[str, Any]], session=None) -> int:
    """
    INSERT ... ON CONFLICT (user_id, night, target_key) DO NOTHING: the first
    showing wins. Runs off the request path (FastAPI BackgroundTasks); never raises.
    """
    if not rows:
        return 0
    from app.models.recommendation import RecommendationImpression

    now = datetime.utcnow()
    values = [{"user_id": user_id, "night": _as_date(row["night"]), "target_key": row["target_key"],
               "site_id": row.get("site_id"), "rig_mode": row.get("rig_mode"), "rig_id": row.get("rig_id"),
               "lane": row.get("lane"), "rank": row.get("rank"), "score": row.get("score"),
               "is_hero": bool(row.get("is_hero")), "first_shown_at": now} for row in rows]
    try:
        with _session_scope(session) as s:
            try:
                res = s.execute(_dialect_insert(s, RecommendationImpression.__table__).values(values)
                                .on_conflict_do_nothing(index_elements=["user_id", "night", "target_key"]))
                s.commit()
            except Exception:
                s.rollback()
                raise
            return int(res.rowcount or 0) if res.rowcount is not None and res.rowcount >= 0 else 0
    except Exception as e:
        logger.warning(f"Recording recommendation impressions failed: {e}")
        return 0


IMPRESSIONS_SQL = text("""
    SELECT night, target_key, lane, is_hero FROM recommendation_impressions
    WHERE user_id = :user_id AND night >= :since
""")

# (raw key, night) pairs with light subs. Untargeted frames (key NULL) still
# mark a night with imaging. capture_date is pre-filtered loosely; the exact
# night window is applied in Python.
IMAGED_SQL = text(f"""
    SELECT CASE WHEN images.target_source IS DISTINCT FROM 'NONE' THEN images.target_key END AS key,
           {NIGHT_SQL} AS night
    FROM images {NIGHT_JOIN_SQL}
    WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
      AND images.exposure_time_seconds > 0 AND images.capture_date IS NOT NULL
      AND images.capture_date >= :since_ts
    GROUP BY 1, 2
""")

IMAGED_EVENTS_SQL = text("""
    SELECT night, target_key FROM recommendation_events
    WHERE user_id = :user_id AND action = 'IMAGED' AND night >= :since
""")


def get_outcomes(user_id: int, days: int = 90, session=None, today: Optional[date] = None) -> Dict[str, Any]:
    """GET /api/recommendations/outcomes?days= (the user's own impressions)."""
    today = today or datetime.utcnow().date()
    since = today - timedelta(days=int(days))
    r = _redis()
    with _session_scope(session) as s:
        impressions = [Impression(_as_date(row["night"]), row["target_key"], row["lane"], bool(row["is_hero"]))
                       for row in s.execute(IMPRESSIONS_SQL, {"user_id": user_id, "since": since}).mappings().all()]
        imaged_rows = [ImagedRow(row["key"], _as_date(row["night"]))
                       for row in s.execute(IMAGED_SQL, {"since_ts": datetime.combine(since - timedelta(days=2),
                                                                                      datetime.min.time())})
                       .mappings().all()]
        events = [(_as_date(row["night"]), row["target_key"])
                  for row in s.execute(IMAGED_EVENTS_SQL, {"user_id": user_id, "since": since}).mappings().all()]
        key_map = _pool_for_keys(s, r).key_map if imaged_rows else {}
    imaged, nights = imaged_by_night((row for row in imaged_rows if row.night is not None and row.night >= since),
                                     key_map)
    return compute_outcomes(impressions, imaged, nights, events, since, today)


def query_pair_rows(session) -> list:
    """Replay: PairRow(key, night, lat, source, seconds) per (target, night, latitude, source)."""
    from app.services.recommend.replay import PairRow

    return [PairRow(r["key"], _as_date(r["night"]), float(r["lat"]) if r["lat"] is not None else None,
                    r["source"], float(r["seconds"] or 0.0))
            for r in session.execute(PAIRS_SQL).mappings().all()]
