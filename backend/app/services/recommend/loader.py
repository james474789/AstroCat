"""
DB -> engine inputs, caching and the request-level service (R1 spec §6). Not pure.

Uses a sync session (Celery, scripts, and the API through run_in_threadpool).

Caches:
- in process: the candidate pool (rebuilt when the alias index is rebuilt or
  the set of imaged keys changes) and the history inputs per hist_version;
- Redis `recs:inputs:v<hist_version>` (history rows, masters, goals; 1 h);
- Redis `recs:result:<site>:<rig>:<night>:<per_lane>:<hist_version>` (6 h).
hist_version = max(images.updated_at) + count of light subs + target_goals
count/max(updated_at). Equipment/site/mount/horizon writes and every
assign_equipment run delete `recs:*`.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select, text

from app.services.recommend import (
    RIG_MODE_ALL, RIG_MODE_ALL_FALLBACK, RIG_MODE_MOUNTED, RIG_MODE_SINGLE, EngineInputs, Params, explain_target,
    recommend, result_to_dict,
)
from app.services.recommend.candidates import CandidatePool, build_candidates
from app.services.recommend.context import HorizonSpec, SiteSpec, resolve_horizon
from app.services.recommend.history import (
    CLASS_BB, CLASS_OSC, GoalRow, HistoryRow, band_classes, build_history, filter_classes, infer_goals, sort_rows,
)
from app.services.recommend.scoring import RigSpec
from app.utils.filter_names import normalize_filter
from app.utils.observing_night import NIGHT_JOIN_SQL, NIGHT_SQL
from app.utils.optics import fov_deg, pixel_scale

logger = logging.getLogger(__name__)

INPUTS_CACHE_KEY = "recs:inputs:v{version}"
INPUTS_CACHE_TTL = 3600
RESULT_CACHE_KEY = "recs:result:{site}:{rig}:{night}:{per_lane}:{version}"
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

_pool_memo: Dict[str, Any] = {"index_id": None, "imaged": None, "pool": None}


def load_pool(session, imaged_keys: frozenset) -> CandidatePool:
    from app.models.catalog import CaldwellCatalog, MessierCatalog, NGCCatalog, Sh2Catalog
    from app.services.targets import get_alias_index_sync

    index = get_alias_index_sync(session)
    if (_pool_memo["pool"] is not None and _pool_memo["index_id"] == id(index)
            and _pool_memo["imaged"] == imaged_keys):
        return _pool_memo["pool"]
    messier = session.execute(select(MessierCatalog)).scalars().all()
    ngc = session.execute(select(NGCCatalog)).scalars().all()
    caldwell = session.execute(select(CaldwellCatalog)).scalars().all()
    sh2 = session.execute(select(Sh2Catalog)).scalars().all()
    pool = CandidatePool(build_candidates(messier, caldwell, ngc, sh2, index, imaged_keys))
    _pool_memo.update(index_id=id(index), imaged=imaged_keys, pool=pool)
    return pool


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
    pool = load_pool(session, hist_inputs.imaged_keys)
    history = build_history(hist_inputs.rows, as_of=as_of, masters=hist_inputs.masters, presorted=True)
    goals = infer_goals(history, {c.key: c.kind for c in pool.candidates}, hist_inputs.goal_rows, as_of)
    records = load_rigs(session, hist_inputs.rows)
    specs, mode, skipped = select_rigs(records, rig)
    horizon = site_horizon(session, site, r)
    inputs = EngineInputs(candidates=pool, history=history, site=site, horizon=horizon, rigs=specs, goals=goals,
                          night=night, now=datetime.utcnow(), skipped_rigs=skipped)
    return inputs, mode


def _session_scope(session):
    if session is not None:
        from contextlib import nullcontext
        return nullcontext(session)
    from app.database import SessionLocal
    return SessionLocal()


def get_recommendations(night: Optional[date] = None, site_id: Optional[int] = None, rig: str = "mounted",
                        per_lane: int = 6, use_cache: bool = True, session=None) -> Dict[str, Any]:
    """The §7 GET /api/recommendations body. Raises RecommendationError for 4xx cases."""
    r = _redis()
    with _session_scope(session) as s:
        site = pick_site(load_sites(s), site_id)
        night = night or default_night(datetime.utcnow(), site.longitude)
        version = hist_version(s)
        key = RESULT_CACHE_KEY.format(site=site.id, rig=(rig or "mounted").lower(), night=night.isoformat(),
                                      per_lane=per_lane, version=version)
        if use_cache:
            cached = _cache_get(r, key)
            if cached:
                cached["cached"] = True
                return cached
        started = time.time()
        inputs, mode = build_inputs(s, site, night, rig, version, r)
        result = recommend(inputs, Params(rig_mode=mode, per_lane=per_lane))
        body = result_to_dict(result, datetime.utcnow(), cached=False)
        logger.info(f"Recommendations for site {site.id} night {night} rig={rig}: "
                    f"{len(result.ranked)} feasible in {time.time() - started:.2f}s")
    _cache_set(r, key, body, RESULT_CACHE_TTL)
    return body


def get_target_explanation(key: str, night: Optional[date] = None, site_id: Optional[int] = None,
                           rig: str = "mounted", session=None) -> Dict[str, Any]:
    """The per-target "why / why not" body. 404 (RecommendationError) when the key isn't a candidate."""
    r = _redis()
    with _session_scope(session) as s:
        site = pick_site(load_sites(s), site_id)
        night = night or default_night(datetime.utcnow(), site.longitude)
        inputs, mode = build_inputs(s, site, night, rig, None, r)
        pool = inputs.pool
        resolved = key if key in pool.index else None
        if resolved is None:
            from app.services.targets import get_alias_index_sync, normalize_designation
            alias = get_alias_index_sync(s).resolve(key)
            for k in (alias, normalize_designation(key)):
                if k and k in pool.index:
                    resolved = k
                    break
        if resolved is None:
            raise RecommendationError(404, f"'{key}' is not in the candidate pool")
        body = explain_target(inputs, Params(rig_mode=mode), resolved)
    body["rig_mode"] = mode
    body["skipped_rigs"] = inputs.skipped_rigs
    return body
