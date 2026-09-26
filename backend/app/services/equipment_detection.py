"""
Equipment detection (R0, docs/design/P0-R0-equipment-sites.md §4.3).

Proposes cameras, optics, filters, rigs and sites from library history.
Nothing is created here: the user accepts proposals explicitly
(POST /api/equipment/detect/apply, which runs `plan_apply`).

- `normalize_camera_name(name)`: the matching key for a camera name
  ("Canon Canon EOS R7" -> "canon eos r7", "Canon [EOS R8]" -> "canon eos r8").
- `propose(buckets, image_sites, existing)`: pure core. `buckets` are
  pre-aggregated light-sub rows (see `fetch_buckets_sync` for the SQL).
- `plan_apply(proposals, accept, default_timezone)`: pure; which rows to
  create for an accept list.
"""

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.capture_time import unchar
from app.utils.filter_names import filter_sort_key, normalize_filter
from app.utils.rig_optics import (
    ARCSEC_PER_RADIAN_OVER_1000, CLUSTER_TOLERANCE, binning_factor, cluster_by_scale, dims_match,
    parse_pixel_size, seed_sensor, valid_pixel_scale, weighted_median,
)

LOOKBACK_MONTHS = 36
JUNK_MIN_SUBS = 50            # junk camera names (phones, "notAvailable") need this many subs
CAMERA_MIN_SUBS = 10          # any camera proposal needs this many subs
RIG_MIN_SUBS = 20             # a scale cluster needs this many subs to become a rig
MIN_SENSOR_SIDE_PX = 320      # smaller frames are thumbnails / previews
SITE_CLUSTER_KM = 5.0         # ~0.05 deg
SITE_MIN_FRAMES = 200
SITE_MATCH_KM = 10.0
OPTIC_MATCH_TOLERANCE = 0.05
COMMON_MODIFIERS = (1.0, 0.8, 0.7)

DSLR_MAKERS = {"canon", "nikon", "sony", "fujifilm", "fuji", "pentax", "olympus", "panasonic", "leica"}
# Model-name prefixes that imply a maker when the header omits it.
_IMPLIED_MAKER = {"eos": "canon", "asi": "zwo", "qhy": "qhyccd"}
_JUNK_NAMES = {"notavailable", "not available", "unknown", "none", "n/a", "na", "null", "camera", "default"}
_PHONE_RE = re.compile(r"\b(samsung|sm[ -]?[a-z]\d{3,}[a-z]*|iphone|apple|pixel \d|google|huawei|xiaomi|oneplus|oppo|redmi|motorola)\b")
_MONO_RE = re.compile(r"\d\s*mm\b|\bmono\b|\d+m\b")
_COLOR_RE = re.compile(r"\d\s*mc\b|\d+c\b|\bosc\b|\bcolou?r\b")


# ---------------------------------------------------------------------------
# Camera names
# ---------------------------------------------------------------------------

def normalize_camera_name(name: Any) -> Optional[str]:
    """
    Lowercase matching key: brackets and '-'/'_' become spaces, whitespace
    collapses, a repeated leading maker collapses ("canon canon eos r7"), and
    an implied maker is added ("eos r8" -> "canon eos r8"). None for blanks.
    """
    name = unchar(name)
    if name is None:
        return None
    text = str(name).strip().strip("\x00").lower()
    text = re.sub(r"[\[\]\(\)\{\}_\"']", " ", text)
    text = re.sub(r"\s*-\s*", " ", text)
    tokens = text.split()
    if not tokens:
        return None
    while len(tokens) > 1 and tokens[0] == tokens[1]:
        tokens.pop(0)
    implied = None
    for prefix, maker in _IMPLIED_MAKER.items():
        if tokens[0].startswith(prefix) and tokens[0] != maker:
            implied = maker
            break
    if implied and (len(tokens) < 2 or tokens[0] != implied):
        tokens.insert(0, implied)
    return " ".join(tokens)


def is_junk_camera(key: Optional[str]) -> bool:
    if not key:
        return True
    return key in _JUNK_NAMES or bool(_PHONE_RE.search(key))


def display_camera_name(raw_names: Counter) -> str:
    """Most common raw spelling with a doubled maker collapsed ("Canon Canon EOS R7" -> "Canon EOS R7")."""
    raw = raw_names.most_common(1)[0][0] if raw_names else ""
    raw = re.sub(r"[\[\]]", "", str(raw)).strip()
    tokens = raw.split()
    while len(tokens) > 1 and tokens[0].lower() == tokens[1].lower():
        tokens.pop(0)
    return " ".join(tokens)[:100] or "Camera"


def camera_maker(key: str) -> Optional[str]:
    first = key.split()[0] if key else ""
    if first in DSLR_MAKERS or first in ("zwo", "qhyccd", "qhy", "fli", "sbig", "atik", "moravian", "player", "touptek"):
        return {"zwo": "ZWO", "qhyccd": "QHY", "qhy": "QHY", "fli": "FLI", "sbig": "SBIG"}.get(first, first.capitalize())
    return None


def infer_is_color(key: str, bayer_fraction: float) -> Optional[bool]:
    if bayer_fraction >= 0.5:
        return True
    first = key.split()[0] if key else ""
    if first in DSLR_MAKERS:
        return True
    if _COLOR_RE.search(key):
        return True
    if _MONO_RE.search(key):
        return False
    return None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _iso(dt: Any) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else (dt if isinstance(dt, str) else None)


def _later(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _num(value: Any) -> Optional[float]:
    value = unchar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(value))
        if not m:
            return None
        v = float(m.group(0))
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _existing_camera_for(key: str, dims: Tuple[int, int], existing_cameras: List[dict]) -> Optional[dict]:
    """Existing camera whose pattern/name matches the key and whose dims fit (or are unknown)."""
    best = None
    for cam in existing_cameras:
        patterns = [str(p).lower() for p in (cam.get("match_patterns") or []) if p]
        name_key = normalize_camera_name(cam.get("name"))
        if not (any(p and p in key for p in patterns) or (name_key and (name_key == key or name_key in key))):
            continue
        w, h = cam.get("sensor_width_px"), cam.get("sensor_height_px")
        if w and h:
            if dims_match(dims[0], dims[1], w, h):
                return cam
            continue
        best = best or cam
    return best


def _match_optic(predicted_focal: float, optics: List[dict]) -> Optional[Tuple[dict, float]]:
    """(optic, modifier_factor) whose focal x common modifier is within 5% of predicted; factor 1.0 preferred."""
    best = None
    for opt in optics:
        fl = opt.get("focal_length_mm")
        if not fl:
            continue
        for factor in COMMON_MODIFIERS:
            err = abs(fl * factor - predicted_focal) / predicted_focal
            if err <= OPTIC_MATCH_TOLERANCE:
                rank = (0 if factor == 1.0 else 1, err)
                if best is None or rank < best[0]:
                    best = (rank, opt, factor)
    return (best[1], best[2]) if best else None


def _camera_groups(buckets: List[dict]) -> Dict[str, dict]:
    """key -> {dims -> group}. Binned frames fold into the unbinned sensor dims."""
    by_key: Dict[str, Dict[Tuple[int, int], dict]] = defaultdict(dict)
    for b in buckets:
        key = normalize_camera_name(b.get("camera_name"))
        w, h = b.get("width_pixels"), b.get("height_pixels")
        if not key or not w or not h:
            continue
        if min(w, h) < MIN_SENSOR_SIDE_PX:
            continue
        binning = binning_factor(b.get("binning"))
        dims = (int(w) * binning, int(h) * binning)
        dims = (max(dims), min(dims))
        groups = by_key[key]
        target = next((d for d in groups if dims_match(dims[0], dims[1], d[0], d[1], tol=0.02)), None)
        if target is None:
            target = dims
            groups[target] = {"dims": dims, "buckets": [], "count": 0, "raw_names": Counter(),
                              "last_used": None, "orient": Counter()}
        g = groups[target]
        g["buckets"].append(b)
        count = int(b.get("count") or 0)
        g["count"] += count
        g["raw_names"][str(unchar(b.get("camera_name"))).strip()] += count
        g["orient"]["landscape" if w >= h else "portrait"] += count
        g["last_used"] = _later(g["last_used"], b.get("last_used"))
    return by_key


def propose(buckets: Iterable[Dict[str, Any]], image_sites: Iterable[Dict[str, Any]],
            existing: Optional[Dict[str, List[dict]]] = None, *,
            lookback_months: Optional[int] = LOOKBACK_MONTHS,
            generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    """
    buckets: [{camera_name, width_pixels, height_pixels, binning, xpixsz,
               bayer (bool), focallen, pixel_scale, filter_name, count, last_used}]
             from light sub-frames (pixel_scale may be a rounded bucket value).
    image_sites: [{lat, lon, site_name, count, last_used}] (pre-rounded).
    existing: {"cameras": [...], "optics": [...], "filters": [...], "rigs": [...], "sites": [...]}
              as plain dicts (rigs carry camera_id/optic_id/binning/modifier_factor/declared_scale).
    Returns the GET /api/equipment/detect payload.
    """
    existing = existing or {}
    ex_cameras = existing.get("cameras") or []
    ex_optics = existing.get("optics") or []
    ex_filters = existing.get("filters") or []
    ex_rigs = existing.get("rigs") or []
    ex_sites = existing.get("sites") or []
    buckets = list(buckets)

    cameras_out: List[dict] = []
    optics_out: Dict[str, dict] = {}
    rigs_out: List[dict] = []

    for key, groups in sorted(_camera_groups(buckets).items()):
        total = sum(g["count"] for g in groups.values())
        if is_junk_camera(key) and total < JUNK_MIN_SUBS:
            continue
        ordered = sorted(groups.values(), key=lambda g: -g["count"])
        for rank, g in enumerate(ordered):
            if g["count"] < CAMERA_MIN_SUBS or (rank > 0 and g["count"] < JUNK_MIN_SUBS):
                continue
            dims = g["dims"]
            if g["orient"]["portrait"] > g["orient"]["landscape"]:
                width, height = dims[1], dims[0]
            else:
                width, height = dims
            seed = seed_sensor(key, width, height)

            # Pixel size: header XPIXSZ (binned) / binning, median by count; else seed.
            pix_pairs = []
            for b in g["buckets"]:
                px = parse_pixel_size(unchar(b.get("xpixsz")))
                if px:
                    pix_pairs.append((round(px / binning_factor(b.get("binning")), 3), b.get("count") or 0))
            pixel_um = weighted_median(pix_pairs)
            if pixel_um is None and seed and dims_match(width, height, seed.get("width"), seed.get("height")):
                pixel_um = seed["pixel_um"]
            elif pixel_um is None and seed and not seed.get("width"):
                pixel_um = seed["pixel_um"]

            bayer_count = sum((b.get("count") or 0) for b in g["buckets"] if b.get("bayer"))
            is_color = infer_is_color(key, bayer_count / g["count"] if g["count"] else 0.0)
            if is_color is None and seed:
                is_color = seed.get("is_color")

            name = display_camera_name(g["raw_names"])
            if rank > 0:
                mode = seed.get("mode") if seed and dims_match(width, height, seed.get("width"), seed.get("height")) else None
                name = f"{name} ({mode})" if mode else f"{name} ({width}x{height})"
            match = _existing_camera_for(key, dims, ex_cameras)
            cam_pid = f"cam:{key}:{width}x{height}"
            cam = {
                "proposal_id": cam_pid,
                "name": name,
                "maker": camera_maker(key) or (seed.get("maker") if seed else None),
                "sensor_width_px": width,
                "sensor_height_px": height,
                "pixel_size_um": round(pixel_um, 3) if pixel_um else None,
                "is_color": is_color,
                "match_patterns": [key],
                "image_count": g["count"],
                "last_used": _iso(g["last_used"]),
                "exists": match is not None,
                "existing_id": match.get("id") if match else None,
            }
            cameras_out.append(cam)
            if match is not None and not cam["pixel_size_um"] and match.get("pixel_size_um"):
                pixel_um = match["pixel_size_um"]
            rigs_out.extend(_propose_rigs(cam, g, pixel_um, match, ex_optics, ex_rigs, optics_out))

    filters_out = _propose_filters(buckets, ex_filters)
    sites_out = propose_sites(image_sites, ex_sites)

    return {
        "generated_at": (generated_at or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(),
        "lookback_months": lookback_months,
        "cameras": cameras_out,
        "optics": sorted(optics_out.values(), key=lambda o: o["focal_length_mm"]),
        "filters": filters_out,
        "rigs": sorted(rigs_out, key=lambda r: -r["image_count"]),
        "sites": sites_out,
    }


def _propose_rigs(cam: dict, group: dict, pixel_um: Optional[float], cam_match: Optional[dict],
                  ex_optics: List[dict], ex_rigs: List[dict], optics_out: Dict[str, dict]) -> List[dict]:
    """One rig proposal per scale cluster (per binning) of a camera group."""
    per_bin: Dict[int, List[dict]] = defaultdict(list)
    for b in group["buckets"]:
        scale = valid_pixel_scale(b.get("pixel_scale"))
        if scale is None:
            continue
        per_bin[binning_factor(b.get("binning"))].append({**b, "_scale": scale})

    rigs = []
    for binning, items in sorted(per_bin.items()):
        for cluster in cluster_by_scale(items, "_scale", CLUSTER_TOLERANCE):
            subs = sum(int(m.get("count") or 0) for m in cluster)
            if subs < RIG_MIN_SUBS:
                continue
            scale = weighted_median([(m["_scale"], int(m.get("count") or 0)) for m in cluster])
            last_used = None
            for m in cluster:
                last_used = _later(last_used, m.get("last_used"))
            fl_votes = Counter()
            for m in cluster:
                fl = _num(m.get("focallen"))
                if fl and fl > 5:
                    fl_votes[round(fl)] += int(m.get("count") or 0)

            if pixel_um:
                predicted = ARCSEC_PER_RADIAN_OVER_1000 * pixel_um * binning / scale
            elif fl_votes:
                predicted = float(fl_votes.most_common(1)[0][0])   # no pixel size: trust FOCALLEN
            else:
                continue

            optic_match = _match_optic(predicted, ex_optics)
            modifier = 1.0
            if optic_match:
                optic, modifier = optic_match
                optic_pid, optic_eid, optic_name = None, optic["id"], optic["name"]
            else:
                optic_pid, optic_name = _proposed_optic(predicted, cam, optics_out)
                optic_eid = None

            bands = sorted({_band(m.get("filter_name")) for m in cluster} - {"None"}, key=filter_sort_key)
            existing_rig = _existing_rig(cam_match, scale, binning, ex_rigs)
            rigs.append({
                "proposal_id": f"rig:{cam['proposal_id'][4:]}:{scale:.2f}" + (f":b{binning}" if binning > 1 else ""),
                "name": f"{optic_name} + {cam['name']}"[:150],
                "camera_proposal_id": cam["proposal_id"],
                "camera_existing_id": cam["existing_id"],
                "optic_proposal_id": optic_pid,
                "optic_existing_id": optic_eid,
                "predicted_focal_mm": round(predicted, 1),
                "modifier_factor": modifier,
                "binning": binning,
                "measured_scale_arcsec": round(scale, 3),
                "filter_bands": bands,
                "image_count": subs,
                "last_used": _iso(last_used),
                "exists": existing_rig is not None,
                "existing_id": existing_rig.get("id") if existing_rig else None,
            })
    return rigs


def _proposed_optic(predicted: float, cam: dict, optics_out: Dict[str, dict]) -> Tuple[str, str]:
    """Reuse a proposed optic within 5% (e.g. two cameras on one scope), else add one."""
    for pid, opt in optics_out.items():
        if abs(opt["focal_length_mm"] - predicted) / predicted <= OPTIC_MATCH_TOLERANCE:
            return pid, opt["name"]
    fl = int(round(predicted))
    is_lens = bool(cam.get("maker") and cam["maker"].lower() in DSLR_MAKERS and predicted <= 600)
    pid = f"opt:{fl}"
    optics_out[pid] = {
        "proposal_id": pid,
        "name": f"~{fl} mm (detected)",
        "kind": "LENS" if is_lens else "TELESCOPE",
        "focal_length_mm": float(fl),
        "aperture_mm": None,
        "exists": False,
        "existing_id": None,
    }
    return pid, optics_out[pid]["name"]


def _existing_rig(cam_match: Optional[dict], scale: float, binning: int, ex_rigs: List[dict]) -> Optional[dict]:
    if not cam_match:
        return None
    for rig in ex_rigs:
        if rig.get("camera_id") != cam_match.get("id") or int(rig.get("binning") or 1) != binning:
            continue
        ref = rig.get("declared_scale") or rig.get("measured_scale_arcsec")
        if ref and abs(ref - scale) / scale <= 0.07:
            return rig
    return None


def _band(filter_name: Any) -> str:
    bucket = normalize_filter(unchar(filter_name) if filter_name is not None else None)
    return "Other" if bucket.startswith("Other:") else bucket


def _propose_filters(buckets: List[dict], ex_filters: List[dict]) -> List[dict]:
    groups: Dict[str, dict] = {}
    for b in buckets:
        raw = unchar(b.get("filter_name"))
        raw = str(raw).strip() if raw is not None else None
        bucket = normalize_filter(raw)
        if bucket == "None":
            continue
        g = groups.setdefault(bucket, {"patterns": Counter(), "count": 0})
        g["patterns"][raw.lower()] += int(b.get("count") or 0)
        g["count"] += int(b.get("count") or 0)

    out = []
    for bucket, g in groups.items():
        band = "Other" if bucket.startswith("Other:") else bucket
        name = bucket[len("Other:"):] if bucket.startswith("Other:") else bucket
        patterns = [p for p, _ in g["patterns"].most_common()]
        match = None
        for f in ex_filters:
            f_patterns = [str(p).lower() for p in (f.get("match_patterns") or [])]
            if band != "Other" and f.get("band") == band:
                match = f
                break
            if (f.get("name") or "").lower() == name.lower() or any(p in f_patterns for p in patterns):
                match = f
                break
        out.append({
            "proposal_id": f"flt:{bucket.lower()}",
            "name": name[:100],
            "band": band,
            "match_patterns": patterns,
            "image_count": g["count"],
            "exists": match is not None,
            "existing_id": match.get("id") if match else None,
        })
    out.sort(key=lambda f: (filter_sort_key(f["band"]), -f["image_count"]))
    return out


def propose_sites(image_sites: Iterable[Dict[str, Any]], ex_sites: List[dict],
                  cluster_km: float = SITE_CLUSTER_KM, min_frames: int = SITE_MIN_FRAMES) -> List[dict]:
    """Greedy clustering of per-image coordinates (largest counts seed clusters)."""
    clusters: List[dict] = []
    for p in sorted(image_sites, key=lambda s: -(s.get("count") or 0)):
        lat, lon, count = p.get("lat"), p.get("lon"), int(p.get("count") or 0)
        if lat is None or lon is None or count <= 0:
            continue
        home = next((c for c in clusters if haversine_km(c["seed"][0], c["seed"][1], lat, lon) <= cluster_km), None)
        if home is None:
            home = {"seed": (lat, lon), "w": 0, "lat_sum": 0.0, "lon_sum": 0.0, "names": Counter(), "last_used": None}
            clusters.append(home)
        home["w"] += count
        home["lat_sum"] += lat * count
        home["lon_sum"] += lon * count
        if p.get("site_name"):
            home["names"][str(p["site_name"]).strip()] += count
        home["last_used"] = _later(home["last_used"], p.get("last_used"))

    out = []
    n = 0
    for c in sorted(clusters, key=lambda c: -c["w"]):
        if c["w"] < min_frames:
            continue
        n += 1
        lat, lon = c["lat_sum"] / c["w"], c["lon_sum"] / c["w"]
        match = next((s for s in ex_sites
                      if haversine_km(s["latitude"], s["longitude"], lat, lon) <= SITE_MATCH_KM), None)
        name = c["names"].most_common(1)[0][0] if c["names"] else f"Site {n}"
        out.append({
            "proposal_id": f"site:{lat:.2f}:{lon:.2f}",
            "name": name[:100],
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "image_count": c["w"],
            "last_used": _iso(c["last_used"]),
            "exists": match is not None,
            "existing_id": match.get("id") if match else None,
        })
    return out


# ---------------------------------------------------------------------------
# Applying accepted proposals (pure planning; the API executes the plan)
# ---------------------------------------------------------------------------

def plan_apply(proposals: Dict[str, Any], accept: List[Dict[str, Any]], default_timezone: str) -> Dict[str, Any]:
    """
    Turn an accept list into creation specs:
    {"cameras": [spec], "optics": [spec], "filters": [spec], "rigs": [spec], "sites": [spec],
     "unknown": [proposal_id]}.
    Camera/optic/filter specs carry "proposal_id" and row fields; rig specs
    reference them by camera_ref/optic_ref ("existing:<id>" or "new:<proposal_id>")
    and filter_refs. Accepting a rig implies its camera, optic and filters.
    Proposals that already exist are never re-created.
    """
    by_id: Dict[str, Tuple[str, dict]] = {}
    for kind in ("cameras", "optics", "filters", "rigs", "sites"):
        for p in proposals.get(kind) or []:
            by_id[p["proposal_id"]] = (kind, p)
    filters_by_band = {f["band"]: f for f in proposals.get("filters") or []}

    plan = {"cameras": [], "optics": [], "filters": [], "rigs": [], "sites": [], "unknown": []}
    planned = set()

    def add(kind: str, proposal: dict, **overrides) -> str:
        if proposal.get("exists") and proposal.get("existing_id"):
            return f"existing:{proposal['existing_id']}"
        pid = proposal["proposal_id"]
        if pid not in planned:
            planned.add(pid)
            spec = {k: v for k, v in proposal.items()
                    if k not in ("exists", "existing_id")}
            spec.update({k: v for k, v in overrides.items() if v is not None})
            plan[kind].append(spec)
        return f"new:{pid}"

    for item in accept:
        pid = item.get("proposal_id")
        if pid not in by_id:
            plan["unknown"].append(pid)
            continue
        kind, p = by_id[pid]
        if kind == "rigs":
            if p.get("exists"):
                continue
            cam = by_id.get(p["camera_proposal_id"], (None, None))[1]
            camera_ref = (f"existing:{p['camera_existing_id']}" if p.get("camera_existing_id")
                          else add("cameras", cam) if cam else None)
            if item.get("optic_id"):
                optic_ref = f"existing:{int(item['optic_id'])}"
            elif p.get("optic_existing_id"):
                optic_ref = f"existing:{p['optic_existing_id']}"
            else:
                opt = by_id.get(p.get("optic_proposal_id"), (None, None))[1]
                optic_ref = add("optics", opt) if opt else None
            if camera_ref is None or optic_ref is None:
                plan["unknown"].append(pid)
                continue
            filter_refs = [add("filters", filters_by_band[b]) for b in p.get("filter_bands") or []
                           if b in filters_by_band]
            plan["rigs"].append({
                "proposal_id": pid,
                "name": (item.get("name") or p["name"])[:150],
                "camera_ref": camera_ref,
                "optic_ref": optic_ref,
                "modifier_factor": float(item.get("modifier_factor") or p.get("modifier_factor") or 1.0),
                "modifier_name": item.get("modifier_name"),
                "binning": int(item.get("binning") or p.get("binning") or 1),
                "filter_refs": filter_refs,
                "measured_scale_arcsec": p.get("measured_scale_arcsec"),
            })
        elif kind == "sites":
            add("sites", p, name=item.get("name"), timezone=item.get("timezone") or default_timezone)
        else:
            add(kind, p, name=item.get("name"))
    return plan


# ---------------------------------------------------------------------------
# SQL wrapper (sync, used by the API via run_sync)
# ---------------------------------------------------------------------------

BUCKETS_SQL = """
    SELECT camera_name, width_pixels, height_pixels, binning,
           raw_header->'XPIXSZ' AS xpixsz,
           (raw_header ? 'BAYERPAT') AS bayer,
           COALESCE(raw_header->'FOCALLEN', to_jsonb(focal_length)) AS focallen,
           round(pixel_scale_arcsec::numeric, 2) AS pixel_scale,
           filter_name,
           count(*) AS count,
           max(capture_date) AS last_used
    FROM images
    WHERE frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
      {since}
    GROUP BY camera_name, width_pixels, height_pixels, binning, raw_header->'XPIXSZ',
             (raw_header ? 'BAYERPAT'), COALESCE(raw_header->'FOCALLEN', to_jsonb(focal_length)),
             round(pixel_scale_arcsec::numeric, 2), filter_name
"""

SITES_SQL = """
    SELECT round(site_latitude::numeric, 2) AS lat, round(site_longitude::numeric, 2) AS lon,
           site_name, count(*) AS count, max(capture_date) AS last_used
    FROM images
    WHERE frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
      AND site_latitude IS NOT NULL AND site_longitude IS NOT NULL
      {since}
    GROUP BY 1, 2, site_name
"""


def fetch_detection_inputs(conn, include_older: bool = False) -> Tuple[List[dict], List[dict]]:
    """Run the aggregation SQL on a sync connection/session. Returns (buckets, image_sites)."""
    from sqlalchemy import text

    since = "" if include_older else f"AND capture_date >= now() - interval '{LOOKBACK_MONTHS} months'"
    buckets = []
    for r in conn.execute(text(BUCKETS_SQL.format(since=since))).mappings():
        row = dict(r)
        row["pixel_scale"] = float(row["pixel_scale"]) if row["pixel_scale"] is not None else None
        buckets.append(row)
    sites = []
    for r in conn.execute(text(SITES_SQL.format(since=since))).mappings():
        row = dict(r)
        row["lat"] = float(row["lat"])
        row["lon"] = float(row["lon"])
        sites.append(row)
    return buckets, sites
