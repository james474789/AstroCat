"""
Frame Type Classifier (F1)

Pure, side-effect-free classification of an image's acquisition frame type
(LIGHT / DARK / FLAT / BIAS / DARK_FLAT) from FITS/XISF header metadata,
the file name, or the directory tree it lives in.

No database access happens here. See docs/design/F1-frame-types.md §3.3 for
the full specification this module implements.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.models.image import FrameType

logger = logging.getLogger(__name__)


@dataclass
class FrameTypeResult:
    frame_type: FrameType
    source: str  # HEADER | FILENAME | PATH | DEFAULT
    is_master_hint: bool = False


# ---------------------------------------------------------------------------
# Header classification
# ---------------------------------------------------------------------------

# Checked in this order (see docs/design/F1-frame-types.md §3.3).
HEADER_KEYS = ("IMAGETYP", "FRAMETYP", "FRAME", "IMAGETYPE")

_STRIP_WORDS = {"frame", "field", "master", "calibrated"}

# Order matters: DARK_FLAT must be checked before DARK/FLAT/BIAS.
_HEADER_PATTERNS = [
    (("dark flat", "flat dark", "darkflat", "flatdark"), FrameType.DARK_FLAT),
    (("bias", "offset", "zero"), FrameType.BIAS),
    (("dark",), FrameType.DARK),
    (("flat",), FrameType.FLAT),
    (("light", "object", "science"), FrameType.LIGHT),
]


def _normalize_header_value(raw) -> tuple[str, bool]:
    """
    Lowercase, replace separators with spaces, collapse whitespace, and strip
    the "frame"/"field"/"master"/"calibrated" filler words.

    Returns (normalized_value, is_master) where is_master reflects whether
    the word "master" appeared in the value before it was stripped.
    """
    s = str(raw).lower()
    s = re.sub(r"[_\-.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    is_master = bool(re.search(r"\bmaster\b", s))

    tokens = [t for t in s.split(" ") if t and t not in _STRIP_WORDS]
    norm = " ".join(tokens)
    return norm, is_master


def _classify_header_value(norm: str) -> Optional[FrameType]:
    for keywords, frame_type in _HEADER_PATTERNS:
        if any(kw in norm for kw in keywords):
            return frame_type
    return None


def _classify_from_header(raw_header: Optional[dict]) -> Optional[tuple[FrameType, bool]]:
    """Return (frame_type, is_master_hint) or None if no header key classified."""
    if not raw_header or not isinstance(raw_header, dict):
        return None

    # Case-insensitive key lookup: header keys may be any case in
    # XISF-converted dicts.
    lower_map = {}
    for k, v in raw_header.items():
        try:
            lower_map[str(k).lower()] = v
        except Exception:
            continue

    for key in HEADER_KEYS:
        value = lower_map.get(key.lower())
        if value is None:
            continue

        norm, is_master = _normalize_header_value(value)
        frame_type = _classify_header_value(norm)
        if frame_type is not None:
            return frame_type, is_master

        # Value present but unrecognized (e.g. "Tricolor", "Focus"):
        # fall through to filename/path/default, per spec.
        logger.debug(
            "frame_type: unrecognized header value %r for key %s; "
            "falling through to filename/path classification",
            value, key,
        )
        return None

    return None


# ---------------------------------------------------------------------------
# Shared tokenization
# ---------------------------------------------------------------------------

_TOKEN_SPLIT_RE = re.compile(r"[\s_\-.()\[\]]+")


def _tokenize(name: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.split(name.lower()) if t]


# ---------------------------------------------------------------------------
# File name classification
# ---------------------------------------------------------------------------

_FILENAME_DARKFLAT_SINGLE = {"darkflat", "darkflats", "flatdark", "flatdarks", "df"}
_FILENAME_BIAS = {"bias", "biases", "offset", "offsets"}
_FILENAME_DARK = {"dark", "darks"}
_FILENAME_FLAT = {"flat", "flats"}
_FILENAME_LIGHT = {"light", "lights"}


def _classify_filename_tokens(tokens: list[str]) -> Optional[FrameType]:
    # DARK_FLAT always wins: single-token forms anywhere in the stem, or the
    # two-token sequences "dark flat" / "flat dark" adjacent anywhere.
    if any(t in _FILENAME_DARKFLAT_SINGLE for t in tokens):
        return FrameType.DARK_FLAT
    for i in range(len(tokens) - 1):
        pair = (tokens[i], tokens[i + 1])
        if pair == ("dark", "flat") or pair == ("flat", "dark"):
            return FrameType.DARK_FLAT

    # Otherwise, the first token (in stem order) that matches any keyword
    # set decides.
    for t in tokens:
        if t in _FILENAME_BIAS:
            return FrameType.BIAS
        if t in _FILENAME_DARK:
            return FrameType.DARK
        if t in _FILENAME_FLAT:
            return FrameType.FLAT
        if t in _FILENAME_LIGHT:
            return FrameType.LIGHT

    return None


def _classify_from_filename(file_path: str) -> Optional[FrameType]:
    norm_path = file_path.replace("\\", "/")
    fname = norm_path.rsplit("/", 1)[-1]
    stem = fname.rsplit(".", 1)[0] if "." in fname else fname
    tokens = _tokenize(stem)
    return _classify_filename_tokens(tokens)


# ---------------------------------------------------------------------------
# Directory (path) classification
# ---------------------------------------------------------------------------

_DIR_KEYWORD_TO_TYPE = {}
for _keys, _ft in (
    (("darkflat", "darkflats", "flatdark", "flatdarks", "df"), FrameType.DARK_FLAT),
    (("bias", "biases", "offset", "offsets"), FrameType.BIAS),
    (("dark", "darks"), FrameType.DARK),
    (("flat", "flats"), FrameType.FLAT),
    (("light", "lights"), FrameType.LIGHT),
):
    for _k in _keys:
        _DIR_KEYWORD_TO_TYPE[_k] = _ft

_FILTER_NAMES = {"l", "r", "g", "b", "ha", "oiii", "sii", "lum", "red", "green", "blue"}
_NUMERIC_TOKEN_RE = re.compile(r"^[-+]?\d")


def _is_nonword_token(tok: str) -> bool:
    if _NUMERIC_TOKEN_RE.match(tok):
        return True
    if tok in _FILTER_NAMES:
        return True
    return False


def _classify_directory_segment(tokens: list[str]) -> Optional[FrameType]:
    """
    Classify a single directory-name segment. The first token must be a
    keyword (or "master" followed by a keyword); every remaining token must
    look like a temperature/exposure/date/gain/filter qualifier, not a
    free-form word. This is what keeps "Dark Sky Site" or "Darkside" from
    misclassifying while "Darks_-10C_300s" still does.
    """
    if not tokens:
        return None

    if tokens[0] == "master" and len(tokens) > 1 and tokens[1] in _DIR_KEYWORD_TO_TYPE:
        frame_type = _DIR_KEYWORD_TO_TYPE[tokens[1]]
        remaining = tokens[2:]
    elif tokens[0] in _DIR_KEYWORD_TO_TYPE:
        frame_type = _DIR_KEYWORD_TO_TYPE[tokens[0]]
        remaining = tokens[1:]
    else:
        return None

    if all(_is_nonword_token(t) for t in remaining):
        return frame_type
    return None


def _mount_root_for(dir_parts: list[str]) -> int:
    """
    Return the number of leading dir_parts that belong to the configured
    mount root that this path lives under (0 if none match). Directory
    walking never goes above this depth.
    """
    try:
        from app.config import settings
        mount_roots = settings.image_paths_list
    except Exception:
        mount_roots = []

    best_depth = 0
    for root in mount_roots:
        root_norm = root.replace("\\", "/").strip("/")
        if not root_norm:
            continue
        root_parts = root_norm.split("/")
        if len(root_parts) <= len(dir_parts) and dir_parts[: len(root_parts)] == root_parts:
            best_depth = max(best_depth, len(root_parts))
    return best_depth


def _classify_from_path(file_path: str) -> Optional[FrameType]:
    norm_path = file_path.replace("\\", "/")
    parts = [p for p in norm_path.split("/")]
    dir_parts = [p for p in parts[:-1] if p]

    root_depth = _mount_root_for(dir_parts)
    segment_names = dir_parts[root_depth:]

    # Nearest directory first (i.e. walk the list in reverse).
    for seg in reversed(segment_names):
        tokens = _tokenize(seg)
        frame_type = _classify_directory_segment(tokens)
        if frame_type is not None:
            return frame_type

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify_frame_type(raw_header: Optional[dict], file_path: str) -> FrameTypeResult:
    """
    Classify a single image's acquisition frame type.

    Priority: FITS/XISF header -> file name -> directory names -> default LIGHT.
    Never raises.
    """
    try:
        header_result = _classify_from_header(raw_header)
        if header_result is not None:
            frame_type, is_master = header_result
            return FrameTypeResult(frame_type=frame_type, source="HEADER", is_master_hint=is_master)

        file_path = file_path or ""

        filename_type = _classify_from_filename(file_path)
        if filename_type is not None:
            return FrameTypeResult(frame_type=filename_type, source="FILENAME")

        path_type = _classify_from_path(file_path)
        if path_type is not None:
            return FrameTypeResult(frame_type=path_type, source="PATH")

        return FrameTypeResult(frame_type=FrameType.LIGHT, source="DEFAULT")
    except Exception as e:
        # Never raise: classification failure must not break indexing.
        logger.warning(f"frame_type: classification error for {file_path!r}: {e}")
        return FrameTypeResult(frame_type=FrameType.LIGHT, source="DEFAULT")
