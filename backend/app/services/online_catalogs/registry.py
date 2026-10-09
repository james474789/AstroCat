"""
Online catalogs for the sky overlay (O1, docs/design/20261009-O1-online-catalog-overlays.md).

Each spec says where a layer's rows come from (a VizieR table, or IMCCE SkyBoT for
solar-system bodies at the capture time) and how its columns map onto the normalised
row build_objects() expects. All are off until an admin enables them.
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from urllib.parse import quote

GROUP_SOLAR = "Solar system"
GROUP_GALAXIES = "Galaxies"
GROUP_NEBULAE = "Nebulae and clusters"

KIND_VIZIER = "vizier"
KIND_SKYBOT = "skybot"

# What an admin "limit" means for a catalog (None: no limit)
LIMIT_MAG = "mag"          # faintest magnitude shown
LIMIT_SIZE = "size"        # smallest diameter shown, arcmin


def simbad_url(ident: str) -> str:
    return f"https://simbad.cds.unistra.fr/simbad/sim-id?Ident={quote(ident)}"


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _i(v: Any) -> Optional[int]:
    f = _f(v)
    return int(f) if f is not None else None


# ---- row mappers: VizieR record (column -> python value) -> normalised row fields -----------
# Each returns None to skip the record. ra/dec/catalog are filled in by the caller.

def _pgc(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pgc = _i(r.get("PGC"))
    if pgc is None:
        return None
    log_d, log_r = _f(r.get("logD25")), _f(r.get("logR25"))
    major = 0.1 * 10 ** log_d if log_d is not None else None      # D25 in 0.1 arcmin, log
    minor = major / 10 ** log_r if major and log_r is not None else None
    mtype = _s(r.get("MType"))
    return {
        "designation": f"PGC {pgc}",
        "aliases": (_s(r.get("ANames")) or "").split(),
        "object_type": f"Galaxy ({mtype})" if mtype else "Galaxy",
        "major": major, "minor": minor, "pa": _f(r.get("PA")),
        "url": f"https://leda.univ-lyon1.fr/ledacat.cgi?o=PGC{pgc}",
    }


def _ldn(r):
    n = _i(r.get("LDN"))
    if n is None:
        return None
    area = _f(r.get("Area"))                                       # deg^2 -> equivalent diameter
    opacity = _i(r.get("Opacity"))
    return {
        "designation": f"LDN {n}",
        "object_type": f"Dark nebula (opacity {opacity})" if opacity else "Dark nebula",
        "major": 2 * math.sqrt(area / math.pi) * 60 if area and area > 0 else None,
        "url": simbad_url(f"LDN {n}"),
    }


def _lbn(r):
    n = _i(r.get("Seq"))
    if n is None:
        return None
    name = _s(r.get("Name"))
    return {
        "designation": f"LBN {n}",
        "aliases": [name] if name else [],
        "object_type": "Bright nebula",
        "major": _f(r.get("Diam1")), "minor": _f(r.get("Diam2")),
        "url": simbad_url(f"LBN {n}"),
    }


def _barnard(r):
    n = _s(r.get("Barn"))
    if not n:
        return None
    return {"designation": f"Barnard {n}", "object_type": "Dark nebula",
            "major": _f(r.get("Diam")), "url": simbad_url(f"Barnard {n}")}


def _vdb(r):
    n = _i(r.get("VdB"))
    if n is None:
        return None
    hd = _i(r.get("HD"))
    return {"designation": f"vdB {n}", "aliases": [f"HD {hd}"] if hd else [],
            "object_type": "Reflection nebula", "magnitude": _f(r.get("Vmag")),
            "url": simbad_url(f"vdB {n}")}


def _pn(r):
    png = _s(r.get("PNG"))
    if not png:
        return None
    name = _s(r.get("Name"))
    if name:
        name = re.sub(r"^A\s+(\d+)$", r"Abell \1", name)      # 'A 39' -> 'Abell 39'
    return {"designation": name or f"PN G{png}", "aliases": [f"PN G{png}"] if name else [],
            "object_type": "Planetary nebula", "url": simbad_url(f"PN G{png}")}


def _abell(r):
    n = _i(r.get("ACO"))
    if n is None:
        return None
    rich = _i(r.get("Rich"))
    z = _f(r.get("z"))
    parts = ["Galaxy cluster"]
    if rich is not None:
        parts.append(f"richness {rich}")
    if z:
        parts.append(f"z={z:.3f}")
    return {"designation": f"Abell {n}", "object_type": ", ".join(parts),
            "magnitude": _f(r.get("m10")), "url": simbad_url(f"ACO {n}")}


def _arp(r):
    n = _i(r.get("Arp"))
    if n is None:
        return None
    name = _s(r.get("Name"))
    # 'NGC 1097' is an alias worth matching on; 'UGC 01810 + 13' isn't
    aliases = [name] if name and "+" not in name else []
    return {"designation": f"Arp {n}", "aliases": aliases, "common_name": name,
            "object_type": "Peculiar galaxy", "major": _f(r.get("Size")), "url": simbad_url(f"Arp {n}")}


@dataclass(frozen=True)
class OnlineCatalogSpec:
    key: str
    label: str
    group: str
    kind: str
    source_name: str                  # credit shown in Admin
    source_url: str
    description: str
    limit_kind: Optional[str] = None
    default_limit: Optional[float] = None
    vizier_tables: Tuple[str, ...] = ()
    vizier_columns: Tuple[str, ...] = ()
    vizier_sort: Optional[str] = None
    mapper: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None
    dedup_local: bool = False         # drop objects the local catalogs already show

    def public(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "group": self.group}

    def describe(self) -> Dict[str, Any]:
        return {**self.public(), "source_name": self.source_name, "source_url": self.source_url,
                "description": self.description, "limit_kind": self.limit_kind,
                "default_limit": self.default_limit}


REGISTRY: Tuple[OnlineCatalogSpec, ...] = (
    OnlineCatalogSpec(
        key="SKYBOT", label="Asteroids & comets", group=GROUP_SOLAR, kind=KIND_SKYBOT,
        source_name="IMCCE SkyBoT", source_url="https://ssp.imcce.fr/webservices/skybot/",
        description="Minor bodies in the frame at the image's capture time (needs a capture date).",
        limit_kind=LIMIT_MAG, default_limit=18.0,
    ),
    OnlineCatalogSpec(
        key="PGC", label="Galaxies (PGC)", group=GROUP_GALAXIES, kind=KIND_VIZIER,
        source_name="HyperLeda PGC via VizieR (VII/237)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/237",
        description="Galaxies OpenNGC doesn't list. Default size limit: about 8 pixels at the image's plate scale.",
        limit_kind=LIMIT_SIZE, default_limit=None,
        vizier_tables=("VII/237/pgc",), vizier_columns=("PGC", "MType", "logD25", "logR25", "PA", "ANames"),
        vizier_sort="-logD25", mapper=_pgc, dedup_local=True,
    ),
    OnlineCatalogSpec(
        key="ARP", label="Arp peculiar galaxies", group=GROUP_GALAXIES, kind=KIND_VIZIER,
        source_name="Arp Atlas via VizieR (VII/192)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/192",
        description="Halton Arp's 338 interacting and peculiar galaxies.",
        vizier_tables=("VII/192/arpord",), vizier_columns=("Arp", "Name", "Size"), mapper=_arp, dedup_local=True,
    ),
    OnlineCatalogSpec(
        key="ABELL", label="Abell galaxy clusters", group=GROUP_GALAXIES, kind=KIND_VIZIER,
        source_name="Abell, Corwin & Olowin via VizieR (VII/110A)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/110A",
        description="Rich clusters of galaxies (ACO), marked at the cluster centre.",
        limit_kind=LIMIT_MAG, default_limit=None,
        vizier_tables=("VII/110A/table3", "VII/110A/table4"), vizier_columns=("ACO", "Rich", "m10", "z"),
        mapper=_abell,
    ),
    OnlineCatalogSpec(
        key="LDN", label="Lynds dark nebulae", group=GROUP_NEBULAE, kind=KIND_VIZIER,
        source_name="Lynds (1962) via VizieR (VII/7A)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/7A",
        description="Dark clouds, drawn as circles of equal area.",
        vizier_tables=("VII/7A/ldn",), vizier_columns=("LDN", "Area", "Opacity"), mapper=_ldn,
    ),
    OnlineCatalogSpec(
        key="LBN", label="Lynds bright nebulae", group=GROUP_NEBULAE, kind=KIND_VIZIER,
        source_name="Lynds (1965) via VizieR (VII/9)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/9",
        description="Emission and reflection nebulae.",
        vizier_tables=("VII/9/catalog",), vizier_columns=("Seq", "Diam1", "Diam2", "Name"), mapper=_lbn,
        dedup_local=True,
    ),
    OnlineCatalogSpec(
        key="BARNARD", label="Barnard dark nebulae", group=GROUP_NEBULAE, kind=KIND_VIZIER,
        source_name="Barnard (1927) via VizieR (VII/220A)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/220A",
        description="Barnard's 349 dark markings in the Milky Way.",
        vizier_tables=("VII/220A/barnard",), vizier_columns=("Barn", "Diam"), mapper=_barnard,
    ),
    OnlineCatalogSpec(
        key="VDB", label="van den Bergh reflection nebulae", group=GROUP_NEBULAE, kind=KIND_VIZIER,
        source_name="van den Bergh (1966) via VizieR (VII/21)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/21",
        description="Reflection nebulae, marked at the illuminating star.",
        vizier_tables=("VII/21/catalog",), vizier_columns=("VdB", "HD", "Vmag"), mapper=_vdb,
    ),
    OnlineCatalogSpec(
        key="PN", label="Planetary nebulae (incl. Abell)", group=GROUP_NEBULAE, kind=KIND_VIZIER,
        source_name="Strasbourg-ESO PN catalogue via VizieR (V/84)", source_url="https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=V/84",
        description="Galactic planetary nebulae, including the faint Abell PNe; ones already in NGC/IC are skipped.",
        vizier_tables=("V/84/main",), vizier_columns=("PNG", "Name"), mapper=_pn, dedup_local=True,
    ),
)

BY_KEY: Dict[str, OnlineCatalogSpec] = {s.key: s for s in REGISTRY}
KEYS: List[str] = [s.key for s in REGISTRY]
