"""
Verify Sky Overlay

Ground-truth accuracy check for the dynamic catalog overlay
(app/services/sky_overlay.py). Read-only; dev use.

1. SOLVER vs solver annotations. For solved images with a job id, fetch the
   solver's own annotation list (/api/jobs/{id}/annotations/: NGC/IC/Messier
   names with pixelx/pixely on the uploaded grid, 1-based FITS pixels), look the names up in our
   catalogs and project them through our SkyFrame, back on the solve grid.
   The signed median dx/dy exposes an origin or half-pixel convention error,
   a large spread exposes a flip or scale error. Residual includes catalog
   position differences (theirs vs OpenNGC), typically a few tenths of a pixel.

2. HEADER vs SOLVER. For images that have both a stored solution and a WCS in
   the file header, project a grid of points through both and report the
   residual per file format, plus the residual after mirroring Y: if the
   mirrored residual is much smaller, that format's header WCS is bottom-up
   (expected risk: PixInsight XISF) and SkyFrame needs a flip for it.

Usage (in the backend container):
    python -m app.scripts.verify_sky_overlay
    python -m app.scripts.verify_sky_overlay --sample 30
"""

import argparse
import re
import statistics
from collections import defaultdict
from types import SimpleNamespace

import httpx
import numpy as np
from sqlalchemy import select, text

from app.config import settings
from app.database import SessionLocal
from app.models.image import Image
from app.services import sky_overlay

NOVA_ROOT = "https://nova.astrometry.net"
_NAME = re.compile(r"^(NGC|IC|M)\s*0*(\d+)\s*([A-Z]?)$", re.IGNORECASE)


def _solver_root(image) -> str:
    if image.plate_solve_provider == "LOCAL" and settings.local_astrometry_url:
        return settings.local_astrometry_url.replace("/api", "").rstrip("/")
    return NOVA_ROOT


def _catalog_positions(session) -> dict:
    """Normalised designation -> (ra, dec) for Messier/NGC/IC."""
    pos = {}
    for sql in ("SELECT designation, ra_degrees, dec_degrees FROM ngc_catalog "
                "WHERE object_type IS NULL OR object_type NOT IN ('Dup', 'NonEx')",
                "SELECT designation, ra_degrees, dec_degrees FROM messier_catalog"):
        for d, ra, dec in session.execute(text(sql)).all():
            pos[sky_overlay.norm_designation(d)] = (ra, dec)
    return pos


def _fetch_annotations(client, root: str, job_id: str) -> list:
    resp = client.get(f"{root}/api/jobs/{job_id}/annotations/")
    resp.raise_for_status()
    return resp.json().get("annotations", [])


def check_solver(session, sample: int) -> None:
    print("== 1. SOLVER projection vs solver annotations (solve-grid pixels) ==")
    positions = _catalog_positions(session)
    images = session.execute(
        select(Image).where(Image.wcs_header.isnot(None), Image.astrometry_job_id.isnot(None))
        .order_by(Image.id.desc()).limit(sample)
    ).scalars().all()
    all_dx, all_dy = [], []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for img in images:
            frame = sky_overlay.resolve_frame(img)
            if frame is None or frame.source != sky_overlay.SOURCE_SOLVER:
                continue
            try:
                annotations = _fetch_annotations(client, _solver_root(img), img.astrometry_job_id)
            except Exception as e:  # network / auth / missing job
                print(f"  image {img.id}: annotations unavailable ({e})")
                continue
            dx, dy = [], []
            for ann in annotations:
                for name in ann.get("names") or []:
                    m = _NAME.match(str(name).strip())
                    if not m:
                        continue
                    key = sky_overlay.norm_designation(f"{m.group(1)}{m.group(2)}{m.group(3)}")
                    if key not in positions:
                        continue
                    ra, dec = positions[key]
                    x, y = frame.project([ra], [dec])
                    gx, gy = frame.from_native(x, y)
                    if np.isfinite(gx[0]) and np.isfinite(gy[0]):
                        # Solver annotations are 1-based FITS pixels; ours are astropy 0-based
                        ddx = float(gx[0]) - (float(ann["pixelx"]) - 1.0)
                        ddy = float(gy[0]) - (float(ann["pixely"]) - 1.0)
                        dx.append(ddx)
                        dy.append(ddy)
                        if np.hypot(ddx, ddy) > 5:
                            print(f"    outlier {key} ({'/'.join(ann.get('names') or [])}): "
                                  f"dx={ddx:+.1f} dy={ddy:+.1f}")
                    break
            if not dx:
                print(f"  image {img.id}: no NGC/IC/Messier annotations to compare")
                continue
            all_dx += dx
            all_dy += dy
            r = [np.hypot(a, b) for a, b in zip(dx, dy)]
            print(f"  image {img.id} ({img.file_format.value if img.file_format else '?'}, "
                  f"grid {frame.grid_w:.0f}x{frame.grid_h:.0f}): n={len(dx)} "
                  f"median dx={statistics.median(dx):+.2f} dy={statistics.median(dy):+.2f} "
                  f"|r| median={statistics.median(r):.2f} max={max(r):.2f}")
    if all_dx:
        r = [np.hypot(a, b) for a, b in zip(all_dx, all_dy)]
        print(f"  ALL: n={len(r)} median dx={statistics.median(all_dx):+.2f} dy={statistics.median(all_dy):+.2f} "
              f"|r| median={statistics.median(r):.2f} p90={np.percentile(r, 90):.2f} max={max(r):.2f} "
              "(target: median |r| < 1 grid px, signed medians ~0)")
    else:
        print("  no comparisons made")


def check_header_vs_solver(session, sample: int) -> None:
    print("== 2. HEADER WCS vs SOLVER solution (native pixels) ==")
    images = session.execute(
        select(Image).where(Image.wcs_header.isnot(None), Image.raw_header.has_key("CRVAL1"))
        .order_by(Image.id.desc()).limit(sample)
    ).scalars().all()
    by_format = defaultdict(lambda: {"direct": [], "mirrored": []})
    for img in images:
        solver = sky_overlay.resolve_frame(img)
        header = sky_overlay.resolve_frame(SimpleNamespace(
            wcs_header=None, raw_header=img.raw_header, width_pixels=img.width_pixels,
            height_pixels=img.height_pixels, file_format=img.file_format, pixel_scale_arcsec=img.pixel_scale_arcsec,
        ))
        if solver is None or header is None or solver.source != sky_overlay.SOURCE_SOLVER:
            continue
        w, h = img.width_pixels, img.height_pixels
        gx, gy = np.meshgrid(np.linspace(0.1 * w, 0.9 * w, 5), np.linspace(0.1 * h, 0.9 * h, 5))
        gx, gy = gx.ravel(), gy.ravel()
        ra, dec = solver.unproject(gx, gy)
        hx, hy = header.project(ra, dec)
        direct = float(np.nanmedian(np.hypot(hx - gx, hy - gy)))
        mirrored = float(np.nanmedian(np.hypot(hx - gx, (h - hy) - gy)))
        fmt = img.file_format.value if img.file_format else "?"
        by_format[fmt]["direct"].append(direct)
        by_format[fmt]["mirrored"].append(mirrored)
        flag = "  <-- Y-FLIP?" if mirrored < direct / 5 else ""
        print(f"  image {img.id} ({fmt}): median residual {direct:.1f} px, Y-mirrored {mirrored:.1f} px{flag}")
    for fmt, v in sorted(by_format.items()):
        print(f"  {fmt}: n={len(v['direct'])} median residual {statistics.median(v['direct']):.1f} px, "
              f"Y-mirrored {statistics.median(v['mirrored']):.1f} px")
    if not by_format:
        print("  no images with both a stored solution and a header WCS")
    # Coverage: how many images would use the (warned) HEADER source, by format
    counts = session.execute(text(
        "SELECT file_format, count(*) FROM images "
        "WHERE wcs_header IS NULL AND raw_header ? 'CRVAL1' GROUP BY file_format ORDER BY 2 DESC"
    )).all()
    print("  HEADER-source images (no stored solution) by format: "
          + (", ".join(f"{f}={n}" for f, n in counts) or "none"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=int, default=20, help="images per check (newest first)")
    args = parser.parse_args()
    with SessionLocal() as session:
        check_solver(session, args.sample)
        print()
        check_header_vs_solver(session, args.sample)


if __name__ == "__main__":
    main()
