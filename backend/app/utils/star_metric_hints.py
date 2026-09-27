"""
Star-quality hints left by capture software (Q1, docs/design/Q1-star-quality.md §4.7).

Reads values the capture software wrote into the FITS/XISF header or the file
name (N.I.N.A. $$HFR$$ / $$STARCOUNT$$ / $$FWHM$$ tokens), at no extra IO:
the header is already stored and the name is known.

Hints are shown alongside AstroCat's own measurement, and used as the value of
record only when AstroCat cannot measure the file (status HINT). They never
feed aggregates: every tool computes HFR differently.
"""

import math
import re
from pathlib import PurePath
from typing import Any, Dict, Optional

# hint name -> header keywords, first present wins
_HEADER_KEYS = {
    "HFR": ("HFR", "HFD_HFR", "STARHFR", "MEANHFR"),
    "FWHM": ("FWHM", "STARFWHM", "MEANFWHM"),
    "STARS": ("STARS", "STARCOUNT", "NSTARS", "STARCNT"),
    "ECCENTRICITY": ("ECCENTRICITY", "ECCENTR", "ECCENTRI"),
    "FOCPOS": ("FOCPOS", "FOCUSPOS", "FOCPOSN", "FOC-POS"),
    "FOCTEMP": ("FOCTEMP", "FOCUSTEM", "FOCUSTMP", "FOC-TEMP"),
    "AMBTEMP": ("AMBTEMP", "AOCAMBT", "TEMPAMB"),
    "AIRMASS": ("AIRMASS",),
    "GUIDE_RMS": ("GUIDERMS", "GUIDE_RMS", "RMSTOTAL"),
}
_TEXT_KEYS = {"PIERSIDE": ("PIERSIDE",)}

_NUM = r"(\d{1,4}(?:[.,]\d{1,4})?)"
_SEP = r"[_\-=\s]?"
# Filename tokens: "HFR_2.31", "HFR2.31", "HFR-2,31", "2.31HFR", "2.31_HFR".
# N.I.N.A. patterns seen in the wild: "_Guide-1.32_", "_HFR-1.85_", "_FWHM-60.77"
# (the FWHM token's unit depends on the plugin that filled it, often arcsec,
# so the FWHM hint is kept as written and never treated as pixels).
_NAME_TOKENS = {
    "HFR": ("HFR",),
    "FWHM": ("FWHM",),
    "STARS": ("STARCOUNT", "STARS"),
    "GUIDE_RMS": ("GUIDERMS", "GUIDE", "RMS"),
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _from_name(stem: str, token: str) -> Optional[float]:
    boundary_before = r"(?:^|(?<=[_\-\s.]))"
    boundary_after = r"(?=$|[_\-\s.])"
    patterns = (
        rf"{boundary_before}{token}{_SEP}{_NUM}{boundary_after}",
        rf"{boundary_before}{_NUM}{_SEP}{token}{boundary_after}",
    )
    for pattern in patterns:
        match = re.search(pattern, stem, flags=re.IGNORECASE)
        if match:
            return _num(match.group(1))
    return None


# N.I.N.A. focuser tokens: "_Focus-122312_" (position, up to 7 digits) and
# "_FTemp--2.37C_" (temperature, may be negative). Empty tokens ("Focus-_",
# "FTemp-C") mean the value wasn't available and don't match.
_FOCUS_POS_RE = re.compile(r"(?:^|(?<=[_\-\s.]))(?:FOCUS|FOCPOS)[_\-=]?(\d{1,7})(?=$|[_\s.])", re.IGNORECASE)
_FOCUS_TEMP_RE = re.compile(r"(?:^|(?<=[_\-\s.]))(?:FTEMP|FOCTEMP)[_\-=]?(-?\d{1,3}(?:[.,]\d{1,3})?)C?(?=$|[_\s.])", re.IGNORECASE)


def extract_hints(raw_header: Optional[dict], file_name: Optional[str]) -> Dict[str, Any]:
    """{hint: value} for every hint found; header values win over filename tokens."""
    hints: Dict[str, Any] = {}
    header = raw_header or {}

    if file_name:
        stem = PurePath(file_name).stem
        for hint, pattern in (("FOCPOS", _FOCUS_POS_RE), ("FOCTEMP", _FOCUS_TEMP_RE)):
            match = pattern.search(stem)
            if match:
                hints[hint] = _num(match.group(1))
        for hint, tokens in _NAME_TOKENS.items():
            for token in tokens:
                value = _from_name(stem, token)
                if value is not None:
                    hints[hint] = value
                    break

    for hint, keys in _HEADER_KEYS.items():
        for key in keys:
            value = _num(header.get(key))
            if value is not None:
                hints[hint] = value
                break
    for hint, keys in _TEXT_KEYS.items():
        for key in keys:
            value = header.get(key)
            if isinstance(value, str) and value.strip():
                hints[hint] = value.strip().upper()
                break

    # A zero HFR/FWHM/star count is the capture software's "not measured".
    for hint in ("HFR", "FWHM", "GUIDE_RMS"):
        if hint in hints and not (0 < hints[hint] < 100):
            del hints[hint]
    if "STARS" in hints:
        if hints["STARS"] <= 0:
            del hints["STARS"]
        else:
            hints["STARS"] = int(hints["STARS"])
    return hints
