"""
Filter Name Normalization (F2)

Maps the wide variety of raw `filter_name` strings captured from FITS headers
and sidecar files (different vendors, bandwidths, and spelling conventions)
onto a small canonical set used for target integration breakdowns.

See docs/design/F2-target-integration.md §3.4 for the full mapping table.
"""

import re
from typing import Optional


# Canonical display order for folded filter breakdowns. "Other:*" buckets
# sort after everything else (handled specially by callers/UI, not here).
FILTER_DISPLAY_ORDER = ["L", "R", "G", "B", "Ha", "OIII", "SII", "Hb", "Duo", "None"]

# CSS colors for each canonical bucket (used by the frontend Targets.css).
# Narrowband hues follow the common bicolor/SHO display convention: Ha and SII
# (both deep-red emission lines) render as two distinct reds, OIII (blue
# channel in the Hubble palette) renders as blue. L is the only neutral/grey
# bucket; Other/None stay grey as the "unclassified" fallback.
FILTER_COLORS = {
    "L": "#d0d4dc",
    "R": "#e05050",
    "G": "#50c070",
    "B": "#5080e0",
    "Ha": "#c8283c",
    "OIII": "#2f80ed",
    "SII": "#a83246",
    "Hb": "#3cc8ff",
    "Duo": "#b060c0",
    "None": "#a0a0a0",
    "Other": "#707070",
}

# Multi-band / narrowband-duo/quad filters. Checked BEFORE generic bandwidth/brand
# noise stripping because stripping "ultra"/"pro" as noise would otherwise mangle
# "L-Ultimate" / "L-Pro" into something that looks like plain "L".
_DUO_KEYWORDS = [
    "l-enhance", "lenhance",
    "l-extreme", "lextreme",
    "l-ultimate", "lultimate",
    "alp-t", "alpt",
    "nbz",
    "triad",
    "quad",
    "duo",
    "dual",
    "tri-band", "triband",
    "cls",
]

# Bandwidth (e.g. "7nm", "3.5nm") and brand-name noise stripped before classification.
_NOISE_PATTERN = re.compile(
    r"\d+(\.\d+)?\s*nm|astrodon|chroma|antlia|baader|optolong|zwo|ultra|pro",
    re.IGNORECASE,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]")

_L_SET = {"l", "lum", "luminance", "clear", "uvir"}
_R_SET = {"r", "red"}
_G_SET = {"g", "green"}
_B_SET = {"b", "blue"}
_HA_SET = {"ha", "halpha", "h"}
_OIII_SET = {"oiii", "o3", "o"}
_SII_SET = {"sii", "s2", "s"}
_HB_SET = {"hb", "hbeta"}

_NONE_UPPER = {"NOFILTER", "OSC", "NONE"}


def _compact(s: str) -> str:
    """Lowercase and strip everything except letters/digits."""
    return _NON_ALNUM.sub("", s.lower())


def normalize_filter(name: Optional[str]) -> str:
    """
    Normalize a raw filter name into one of the canonical buckets:
    L, R, G, B, Ha, OIII, SII, Hb, Duo, None, or Other:<orig>.
    """
    if name is None:
        return "None"

    s = name.strip()
    if not s:
        return "None"

    if s.upper() in _NONE_UPPER:
        return "None"

    # Normalize the Greek letters some tools emit for Ha/Hb before anything else,
    # since they'd otherwise be dropped entirely by alnum-only compaction.
    low = s.lower().replace("α", "a").replace("β", "b")

    compact_all = _compact(low)

    # Duo/quad-band filters: matched on substrings of the fully compacted string
    # (hyphens/spaces/underscores removed) so "L-eXtreme" -> "lextreme" matches.
    for kw in _DUO_KEYWORDS:
        kw_compact = _compact(kw)
        if kw_compact and kw_compact in compact_all:
            return "Duo"

    # L-Pro is a broadband light-pollution filter -> counts as L, not stripped-to-L
    # via the generic "pro" noise word (that path would also match unrelated names).
    if "l-pro" in low or "lpro" in compact_all:
        return "L"

    # Strip bandwidth ("7nm") and brand-name noise, then classify what remains.
    stripped = _NOISE_PATTERN.sub(" ", low)
    compact = _compact(stripped)

    if compact in _HA_SET:
        return "Ha"
    if compact in _OIII_SET:
        return "OIII"
    if compact in _SII_SET:
        return "SII"
    if compact in _HB_SET:
        return "Hb"
    if compact in _L_SET:
        return "L"
    if compact in _R_SET:
        return "R"
    if compact in _G_SET:
        return "G"
    if compact in _B_SET:
        return "B"

    return f"Other:{s}"


def filter_sort_key(bucket: str):
    """Sort key placing canonical buckets in display order, Other:* buckets last (alphabetically)."""
    if bucket in FILTER_DISPLAY_ORDER:
        return (0, FILTER_DISPLAY_ORDER.index(bucket), "")
    return (1, 0, bucket)


def filter_color(bucket: str) -> str:
    """Return the display color for a normalized filter bucket."""
    if bucket in FILTER_COLORS:
        return FILTER_COLORS[bucket]
    return FILTER_COLORS["Other"]
