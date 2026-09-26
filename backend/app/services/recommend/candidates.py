"""
Candidate pool (R1 spec §4.1).

`build_candidates(messier, caldwell, ngc, sh2, alias_index, imaged_keys)`
folds the catalog rows into one Candidate per canonical target key. Keys come
from `services/targets.build_alias_index`, so a candidate key IS an
images.target_key (Messier-first canonicalisation and the Sh2 cross-IDs are
already applied) and history joins directly.

Pure: rows are ORM objects or anything exposing the same attributes.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence

import numpy as np

from app.services.targets import AliasIndex, normalize_designation

KIND_EMISSION = "EMISSION"
KIND_PN = "PN"
KIND_REFLECTION = "REFLECTION"
KIND_GALAXY = "GALAXY"
KIND_CLUSTER = "CLUSTER"
KIND_OTHER = "OTHER"
KINDS = (KIND_EMISSION, KIND_PN, KIND_REFLECTION, KIND_GALAXY, KIND_CLUSTER, KIND_OTHER)

# Row-level classification before merging (ambiguous nebula = Neb / Cl+N).
_ROW_EMISSION = "EMISSION"
_ROW_NEB = "NEB?"

# Catalog familiarity (the `prior` score component).
CATALOG_PRIOR = {"M": 1.0, "C": 0.9, "SH2": 0.6, "NGC": 0.55, "IC": 0.55}

# Curated reflection / broadband nebulae whose catalog type makes them look
# moon-proof (Maia is typed HII). Canonicalised through the alias index at
# build time, so NGC2068 -> M78, C4 -> NGC7023, etc.
REFLECTION_OVERRIDES = frozenset({
    "M45", "M78", "NGC7023", "NGC1333", "IC2118", "NGC1977", "NGC1432", "NGC1435", "C4",
    "NGC2068", "NGC6726", "IC4601", "NGC2245", "NGC2247", "IC348",
})

# NGC/IC rows that never become candidates on their own.
_SKIP_NGC_TYPES = frozenset({"Dup", "NonEx", "*", "**", "Nova", "Other"})

# Imageability gate for NGC/IC-only candidates, and Sh2-only regions.
GATE_MAX_MAG = 11.5
GATE_MIN_SIZE = 8.0
GATE_MIN_SIZE_EMISSION = 3.0
GATE_MIN_SIZE_SH2 = 10.0


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    ra_deg: float
    dec_deg: float
    size_arcmin: Optional[float]
    kind: str
    catalog: str            # M | C | NGC | IC | SH2 (the catalog the key comes from)
    magnitude: Optional[float]
    aliases: FrozenSet[str]
    prior: float = 0.55     # best catalog familiarity across aliases (additive)


class CandidatePool:
    """Candidates plus the numpy arrays the ephemeris and scoring use."""

    def __init__(self, candidates: Sequence[Candidate]):
        self.candidates: List[Candidate] = list(candidates)
        self.index: Dict[str, int] = {c.key: i for i, c in enumerate(self.candidates)}
        self.ra = np.asarray([c.ra_deg for c in self.candidates], dtype=float)
        self.dec = np.asarray([c.dec_deg for c in self.candidates], dtype=float)
        self.size = np.asarray([c.size_arcmin if c.size_arcmin else np.nan for c in self.candidates], dtype=float)
        self.kind = np.asarray([c.kind for c in self.candidates], dtype=object)
        self.prior = np.asarray([c.prior for c in self.candidates], dtype=float)

    def __len__(self):
        return len(self.candidates)

    def get(self, key: str) -> Optional[Candidate]:
        i = self.index.get(key)
        return self.candidates[i] if i is not None else None


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def row_kind(object_type: Optional[str]) -> str:
    """Classify one catalog row's type (OpenNGC short codes or Messier/Caldwell text)."""
    t = (object_type or "").strip().lower()
    if not t:
        return KIND_OTHER
    if t == "pn" or "planetary" in t:
        return KIND_PN
    if t in ("hii", "snr", "emn") or "emission" in t or "supernova" in t or "hii" in t:
        return _ROW_EMISSION
    if t == "rfn" or "reflection" in t:
        return KIND_REFLECTION
    if t in ("neb", "cl+n", "sharpless") or "nebula" in t:
        return _ROW_NEB
    if t == "gcl" or "globular" in t:
        return KIND_CLUSTER
    if t in ("g", "gpair", "gtrpl", "ggroup") or "galax" in t:
        return KIND_GALAXY
    if t in ("ocl", "*ass") or "cluster" in t or "star cloud" in t or "asterism" in t:
        return KIND_CLUSTER
    return KIND_OTHER


def parse_size(value) -> Optional[float]:
    """Major axis in arcmin from a float or a Messier string like '70 x 50' (the largest number)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(value))]
    nums = [n for n in nums if n > 0]
    return max(nums) if nums else None


def key_catalog(key: str) -> str:
    if re.fullmatch(r"M\d+", key):
        return "M"
    if re.fullmatch(r"C\d+", key):
        return "C"
    if key.startswith("SH2"):
        return "SH2"
    if key.startswith("IC"):
        return "IC"
    return "NGC"


def _first_name(common_name: Optional[str]) -> Optional[str]:
    if not common_name:
        return None
    first = str(common_name).split(",")[0].strip()
    return first or None


class _Acc:
    """Accumulates the catalog rows that fold into one canonical key."""

    __slots__ = ("key", "names", "ra", "dec", "size", "mag", "row_kinds", "catalogs", "aliases", "has_sh2")

    def __init__(self, key: str):
        self.key = key
        self.names: List[str] = []
        self.ra: Optional[float] = None
        self.dec: Optional[float] = None
        self.size: Optional[float] = None
        self.mag: Optional[float] = None
        self.row_kinds: List[str] = []
        self.catalogs: set = set()
        self.aliases: set = {key}
        self.has_sh2 = False

    def add(self, catalog: str, designation, ra, dec, size, mag, kind: Optional[str], name, aliases=()):
        self.catalogs.add(catalog)
        for a in (designation, *aliases):
            n = normalize_designation(a) if a else ""
            if n:
                self.aliases.add(n)
        if self.ra is None and ra is not None and dec is not None:
            self.ra, self.dec = float(ra), float(dec)
        if size is not None:
            self.size = size if self.size is None else max(self.size, size)
        if self.mag is None and mag is not None:
            self.mag = float(mag)
        if kind is not None:
            self.row_kinds.append(kind)
        if name:
            self.names.append(name)


def _merged_kind(acc: _Acc, reflection: FrozenSet[str]) -> str:
    if acc.aliases & reflection:
        return KIND_REFLECTION
    kinds = acc.row_kinds
    if KIND_PN in kinds:
        return KIND_PN
    if _ROW_EMISSION in kinds or acc.has_sh2:
        return KIND_EMISSION
    if KIND_REFLECTION in kinds or _ROW_NEB in kinds:
        return KIND_REFLECTION  # unconfirmed nebula: broadband (conservative)
    for k in kinds:  # primary (highest-priority catalog) row decides the rest
        if k in (KIND_GALAXY, KIND_CLUSTER):
            return k
    return KIND_OTHER


def _passes_gate(acc: _Acc, kind: str) -> bool:
    if acc.catalogs & {"M", "C"}:
        return True
    if acc.catalogs == {"SH2"}:
        return (acc.size or 0.0) >= GATE_MIN_SIZE_SH2
    size = acc.size or 0.0
    if acc.mag is not None and acc.mag <= GATE_MAX_MAG:
        return True
    if size >= GATE_MIN_SIZE:
        return True
    return kind == KIND_EMISSION and size >= GATE_MIN_SIZE_EMISSION


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_candidates(messier: Iterable, caldwell: Iterable, ngc: Iterable, sh2: Iterable,
                     alias_index: AliasIndex, imaged_keys: Iterable[str] = ()) -> List[Candidate]:
    """One Candidate per canonical key, gated for imageability (imaged keys always kept)."""
    accs: Dict[str, _Acc] = {}
    order: List[str] = []

    def acc_for(key: str) -> _Acc:
        if key not in accs:
            accs[key] = _Acc(key)
            order.append(key)
        return accs[key]

    def canonical(designation) -> Optional[str]:
        if not designation:
            return None
        return alias_index.resolve(designation) or normalize_designation(designation) or None

    for row in messier:
        key = f"M{row.messier_number}"
        acc_for(key).add("M", row.designation, row.ra_degrees, row.dec_degrees,
                         parse_size(getattr(row, "angular_size_arcmin", None)),
                         getattr(row, "apparent_magnitude", None), row_kind(getattr(row, "object_type", None)),
                         _first_name(getattr(row, "common_name", None)), (getattr(row, "ngc_designation", None),))

    for row in ngc:
        otype = getattr(row, "object_type", None)
        if otype in _SKIP_NGC_TYPES:
            continue
        key = canonical(row.designation)
        if not key:
            continue
        cat = "IC" if normalize_designation(row.designation).startswith("IC") else "NGC"
        acc_for(key).add(cat, row.designation, row.ra_degrees, row.dec_degrees,
                         parse_size(getattr(row, "major_axis_arcmin", None)),
                         getattr(row, "apparent_magnitude", None), row_kind(otype),
                         _first_name(getattr(row, "common_name", None)),
                         (getattr(row, "ic_designation", None), getattr(row, "messier_designation", None)))

    for row in caldwell:
        key = canonical(row.designation)
        if not key:
            continue
        aliases = [a.strip() for a in (getattr(row, "aliases", None) or "").split(",") if a.strip()]
        acc_for(key).add("C", row.designation, row.ra_degrees, row.dec_degrees,
                         parse_size(getattr(row, "major_axis_arcmin", None)),
                         getattr(row, "apparent_magnitude", None), row_kind(getattr(row, "object_type", None)),
                         _first_name(getattr(row, "common_name", None)),
                         (getattr(row, "source_designation", None), *aliases))

    for row in sh2:
        key = canonical(row.designation)
        if not key:
            continue
        acc = acc_for(key)
        # A Sharpless region confirms emission for whatever it merged into.
        acc.has_sh2 = True
        acc.add("SH2", row.designation, row.ra_degrees, row.dec_degrees,
                parse_size(getattr(row, "major_axis_arcmin", None)), getattr(row, "apparent_magnitude", None),
                None, _first_name(getattr(row, "common_name", None)), (getattr(row, "source_designation", None),))

    reflection = frozenset(canonical(k) or k for k in REFLECTION_OVERRIDES) | REFLECTION_OVERRIDES
    imaged = set(imaged_keys or ())
    out: List[Candidate] = []
    for key in order:
        acc = accs[key]
        if acc.ra is None or key.startswith("OBJ:"):
            continue
        kind = _merged_kind(acc, reflection)
        catalogs_only_ngc = not (acc.catalogs & {"M", "C", "SH2"})
        if key not in imaged:
            if catalogs_only_ngc and kind == KIND_OTHER:
                continue
            if not _passes_gate(acc, kind):
                continue
        prior = max(CATALOG_PRIOR.get(c, 0.55) for c in acc.catalogs)
        out.append(Candidate(
            key=key,
            name=acc.names[0] if acc.names else key,
            ra_deg=acc.ra, dec_deg=acc.dec,
            size_arcmin=round(acc.size, 2) if acc.size else None,
            kind=kind, catalog=key_catalog(key), magnitude=acc.mag,
            aliases=frozenset(acc.aliases), prior=prior,
        ))
    return out
