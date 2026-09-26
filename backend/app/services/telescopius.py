"""
Optional Telescopius equipment import (R0, docs/design/P0-R0-equipment-sites.md §4.4).

The API key comes only from the TELESCOPIUS_API_KEY environment variable
(config.Settings.telescopius_api_key). It is never stored, returned by an
endpoint, or logged; errors raised here never include it.

- `fetch_equipment(api_key)`: GET /v2.2/equipment/user.
- `map_telescopius(payload)`: pure mapping to an import plan.
- `merge_plan(plan, existing)`: pure; which rows to create/update/skip.
  `external_ref = "telescopius:<id>"` makes re-imports idempotent.

The payload has no rig combinations and no reducers, and `is_color` is null
for every camera; pixel size is derived from sensor mm / px (~2% error), so
a pixel size measured from XPIXSZ on a detected camera always wins.
"""

import logging
from typing import Any, Dict, List, Optional

from app.utils.filter_names import normalize_filter

logger = logging.getLogger(__name__)

API_URL = "https://api.telescopius.com/v2.2/equipment/user"
TIMEOUT_SECONDS = 20.0
LENS_FOCAL_MAX_MM = 600
LENS_BRANDS = {
    "canon", "nikon", "sigma", "samyang", "rokinon", "tamron", "zeiss", "sony", "fujifilm", "fujinon",
    "pentax", "olympus", "tokina", "laowa", "viltrox", "meike", "leica", "minolta", "contax", "helios",
    "jupiter", "irix", "voigtlander", "walimex", "panasonic", "lumix", "tokina", "ttartisan", "7artisans",
}
PIXEL_NOTE = "Pixel size derived from Telescopius sensor mm/px (approximate, ~2%)."


class TelescopiusError(Exception):
    """Upstream failure; the message never contains the API key."""


def fetch_equipment(api_key: str, timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    import httpx

    if not api_key:
        raise TelescopiusError("Telescopius API key not configured")
    try:
        resp = httpx.get(API_URL, headers={"Authorization": f"Key {api_key}", "Accept": "application/json"},
                         timeout=timeout)
    except httpx.HTTPError as e:
        raise TelescopiusError(f"Telescopius request failed ({type(e).__name__})") from None
    if resp.status_code in (401, 403):
        raise TelescopiusError(f"Telescopius rejected the API key (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise TelescopiusError(f"Telescopius returned HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise TelescopiusError("Telescopius returned a non-JSON response") from None
    if not isinstance(data, dict):
        raise TelescopiusError("Unexpected Telescopius response shape")
    return data


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _int(value: Any) -> Optional[int]:
    v = _num(value)
    return int(round(v)) if v else None


def _label(item: dict) -> Optional[str]:
    for key in ("label", "name", "model_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:100]
    return None


def _ref(kind: str, item: dict, label: str) -> str:
    ident = item.get("id")
    if ident not in (None, ""):
        return f"telescopius:{ident}"[:50]
    return f"telescopius:{kind}:{label.lower()}"[:50]


def _brand(item: dict) -> str:
    for key in ("custom_brand", "brand", "brand_name", "manufacturer"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _is_lens(item: dict, label: str, focal: float) -> bool:
    if focal > LENS_FOCAL_MAX_MM:
        return False
    brand = _brand(item).split()[0] if _brand(item) else ""
    first_word = label.split()[0].lower() if label.split() else ""
    return brand in LENS_BRANDS or first_word in LENS_BRANDS


def map_telescopius(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure mapping of a /equipment/user payload to
    {"cameras": [...], "optics": [...], "filters": [...], "mount_suggestions": [...], "skipped": {...}}.
    """
    from app.services.equipment_detection import normalize_camera_name

    plan = {"cameras": [], "optics": [], "filters": [], "mount_suggestions": [],
            "skipped": {"cameras": 0, "optics": 0, "filters": 0}}
    payload = payload if isinstance(payload, dict) else {}

    for item in payload.get("telescopes") or []:
        label = _label(item) if isinstance(item, dict) else None
        fmax = _num(item.get("focal_length_max")) if label else None
        fmin = _num(item.get("focal_length_min")) if label else None
        focal = fmax or fmin
        if not label or not focal:
            plan["skipped"]["optics"] += 1
            continue
        notes = None
        if fmin and fmax and abs(fmin - fmax) > 0.5:
            notes = f"Zoom {fmin:g}-{fmax:g} mm; imported at {focal:g} mm."
        plan["optics"].append({
            "name": label,
            "kind": "LENS" if _is_lens(item, label, focal) else "TELESCOPE",
            "aperture_mm": _num(item.get("aperture")),
            "focal_length_mm": focal,
            "external_ref": _ref("telescope", item, label),
            "notes": notes,
        })

    for item in payload.get("cameras") or []:
        label = _label(item) if isinstance(item, dict) else None
        if not label:
            plan["skipped"]["cameras"] += 1
            continue
        w_px, h_px = _int(item.get("sensor_width_px")), _int(item.get("sensor_height_px"))
        w_mm = _num(item.get("custom_sensor_width_mm")) or _num(item.get("sensor_width_mm"))
        pixel = round(w_mm / w_px * 1000.0, 3) if (w_mm and w_px) else None
        is_color = item.get("is_color")
        is_cooled = item.get("is_cooled")
        key = normalize_camera_name(label)
        plan["cameras"].append({
            "name": label,
            "maker": (_brand(item).title() or None),
            "sensor_width_px": w_px,
            "sensor_height_px": h_px,
            "pixel_size_um": pixel,
            "is_color": is_color if isinstance(is_color, bool) else None,
            "is_cooled": is_cooled if isinstance(is_cooled, bool) else None,
            "match_patterns": [key] if key else [],
            "external_ref": _ref("camera", item, label),
            "notes": PIXEL_NOTE if pixel else None,
        })

    for item in payload.get("filters") or []:
        label = _label(item) if isinstance(item, dict) else None
        if not label:
            plan["skipped"]["filters"] += 1
            continue
        bucket = normalize_filter(label)
        plan["filters"].append({
            "name": label,
            "band": "Other" if bucket.startswith("Other:") else bucket,
            "match_patterns": [label.lower()],
            "external_ref": _ref("filter", item, label),
        })

    seen = set()
    for item in payload.get("mounts") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name") or item.get("label")
        if isinstance(name, str) and name.strip() and name.strip().lower() not in seen:
            seen.add(name.strip().lower())
            plan["mount_suggestions"].append(name.strip()[:100])
    return plan


# Fields an import may update on an existing row, per kind.
_UPDATABLE = {
    "cameras": ("sensor_width_px", "sensor_height_px", "pixel_size_um", "is_color", "is_cooled", "maker"),
    "optics": ("kind", "aperture_mm", "focal_length_mm", "notes"),
    "filters": ("band",),
}


def merge_plan(plan: Dict[str, Any], existing: Dict[str, List[dict]]) -> Dict[str, Any]:
    """
    Pure: decide per imported row whether to create, update or skip it.
    existing: {"cameras": [row dicts with id, name, external_ref, source, ...], ...}

    - Match by external_ref first, then by case-insensitive name.
    - Updates only fill/overwrite the fields above, and never replace a
      pixel size on a DETECTED/MANUAL camera (header XPIXSZ wins), nor
      non-null values on MANUAL rows.
    - A matched row with nothing to change is skipped.
    Returns {kind: {"create": [spec], "update": [(id, {field: value})], "skipped": int}}.
    """
    result = {}
    for kind in ("cameras", "optics", "filters"):
        rows = existing.get(kind) or []
        by_ref = {r.get("external_ref"): r for r in rows if r.get("external_ref")}
        by_name = {(r.get("name") or "").lower(): r for r in rows}
        out = {"create": [], "update": [], "skipped": (plan.get("skipped") or {}).get(kind, 0)}
        planned_names = set()
        for spec in plan.get(kind) or []:
            row = by_ref.get(spec["external_ref"]) or by_name.get(spec["name"].lower())
            if row is None:
                if spec["name"].lower() in planned_names:
                    out["skipped"] += 1
                    continue
                planned_names.add(spec["name"].lower())
                out["create"].append(spec)
                continue
            changes = {}
            if not row.get("external_ref"):
                changes["external_ref"] = spec["external_ref"]
            manual = row.get("source") == "MANUAL"
            for field in _UPDATABLE[kind]:
                new = spec.get(field)
                if new is None:
                    continue
                old = row.get(field)
                if field == "pixel_size_um" and old and row.get("source") in ("DETECTED", "MANUAL"):
                    continue
                if manual and old is not None:
                    continue
                if old != new and not (isinstance(old, float) and isinstance(new, (int, float))
                                       and abs(old - new) < 1e-6):
                    changes[field] = new
            if changes:
                out["update"].append((row["id"], changes))
            else:
                out["skipped"] += 1
        result[kind] = out
    result["mount_suggestions"] = list(plan.get("mount_suggestions") or [])
    return result
