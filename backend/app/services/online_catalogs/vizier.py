"""
VizieR cone searches over the classic ASU interface (viz-bin/votable).

ASU rather than TAPVizieR: it is the more available service, and it computes J2000
positions (_RAJ2000/_DEJ2000) for catalogs published in B1950 or B1875. The CfA mirror
is tried when CDS fails.
"""

import io
import logging
import math
import warnings
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

from app.services.online_catalogs.registry import OnlineCatalogSpec

logger = logging.getLogger(__name__)

HOSTS = ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu")
TIMEOUT_S = 20.0
MAX_ROWS = 2000


class OnlineCatalogError(Exception):
    """The upstream service failed or answered with an error."""


def query_params(spec: OnlineCatalogSpec, ra: float, dec: float, radius_deg: float,
                 constraints: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    # Decimal degrees with explicit decimals and sign: '68 +26' would be read as sexagesimal
    params = {
        "-source": " ".join(spec.vizier_tables),
        "-c": f"{ra % 360.0:.6f} {dec:+.6f}",
        "-c.rd": f"{radius_deg:.4f}",
        "-out": ",".join(spec.vizier_columns),
        "-out.add": "_RAJ,_DEJ",
        "-out.max": str(MAX_ROWS),
    }
    if spec.vizier_sort:
        params["-sort"] = spec.vizier_sort
    params.update(constraints or {})
    return params


def _value(v: Any) -> Any:
    """Plain python value; None for masked cells, NaN and blank strings."""
    if v is None or v is np.ma.masked:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def parse_votable(content: bytes) -> List[Dict[str, Any]]:
    """Every record of every table in a VizieR VOTable, as {column: value}."""
    if b'name="QUERY_STATUS" value="ERROR"' in content:
        start = content.find(b'name="QUERY_STATUS"')
        raise OnlineCatalogError(f"VizieR error: {content[start:start + 200].decode('utf-8', 'replace')}")
    from astropy.io.votable import parse
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # VizieR VOTables trip harmless spec warnings
        vo = parse(io.BytesIO(content))
        records = []
        for table in vo.iter_tables():
            tab = table.to_table()
            for row in tab:
                records.append({c: _value(row[c]) for c in tab.colnames})
    return records


def to_rows(spec: OnlineCatalogSpec, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """VizieR records -> normalised rows (build_objects() shape, plus url)."""
    rows = []
    for rec in records:
        ra, dec = rec.get("_RAJ2000"), rec.get("_DEJ2000")
        if ra is None or dec is None:
            continue
        mapped = spec.mapper(rec)
        if not mapped:
            continue
        row = {"catalog": spec.key, "aliases": [], "common_name": None, "object_type": None,
               "magnitude": None, "major": None, "minor": None, "pa": None, "url": None}
        row.update(mapped)
        row.update(ra=float(ra), dec=float(dec))
        rows.append(row)
    return rows


async def cone(spec: OnlineCatalogSpec, ra: float, dec: float, radius_deg: float,
               constraints: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    params = query_params(spec, ra, dec, radius_deg, constraints)
    last: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        for host in HOSTS:
            try:
                resp = await client.get(f"{host}/viz-bin/votable", params=params)
                resp.raise_for_status()
                return to_rows(spec, parse_votable(resp.content))
            except Exception as e:
                logger.warning(f"VizieR {spec.key} query failed on {host}: {e}")
                last = e
    raise OnlineCatalogError(f"VizieR unavailable: {last}")
