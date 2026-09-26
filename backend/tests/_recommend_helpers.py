"""Shared synthetic fixtures for the R1 recommendation tests (generic site, no real coordinates)."""

from datetime import date

from app.services.recommend import EngineInputs, Params
from app.services.recommend.candidates import Candidate, CandidatePool
from app.services.recommend.context import HorizonSpec, SiteSpec
from app.services.recommend.history import HistoryRow, build_history, infer_goals
from app.services.recommend.scoring import RigSpec

# Generic Scottish-latitude fixture.
SITE = SiteSpec(id=1, name="Test site", latitude=56.0, longitude=0.0, timezone="Europe/London", is_default=True)
FLAT = HorizonSpec(points=[], source="DEFAULT", floor_deg=30.0)
FULL_MOON_NIGHT = date(2026, 9, 26)


def cand(key, ra, dec, size=30.0, kind="EMISSION", name=None, prior=0.55, catalog="NGC", mag=None):
    return Candidate(key=key, name=name or key, ra_deg=ra, dec_deg=dec, size_arcmin=size, kind=kind,
                     catalog=catalog, magnitude=mag, aliases=frozenset({key}), prior=prior)


def standard_pool():
    return CandidatePool([
        cand("NGC7000", 314.7, 44.3, 120.0, "EMISSION", "North America Nebula", 0.9),
        cand("IC1396", 324.7, 57.5, 170.0, "EMISSION", "Elephant's Trunk", 0.6),
        cand("NGC6960", 311.4, 30.7, 70.0, "EMISSION", "Western Veil", 0.9),
        cand("M31", 10.68, 41.27, 190.0, "GALAXY", "Andromeda Galaxy", 1.0, "M", 3.4),
        cand("M33", 23.46, 30.66, 70.0, "GALAXY", "Triangulum Galaxy", 1.0, "M", 5.7),
        cand("M81", 148.9, 69.07, 27.0, "GALAXY", "Bode's Galaxy", 1.0, "M", 6.9),
        cand("M57", 283.4, 33.03, 1.4, "PN", "Ring Nebula", 1.0, "M", 8.8),
        cand("M45", 56.75, 24.12, 110.0, "REFLECTION", "Pleiades", 1.0, "M", 1.6),
        cand("NGC7635", 350.2, 61.2, 15.0, "EMISSION", "Bubble Nebula", 0.9),
        cand("IC1805", 38.2, 61.45, 150.0, "EMISSION", "Heart Nebula", 0.6),
    ])


NB_RIG = RigSpec(id=1, name="Mono 200mm", scale_arcsec=2.46, fov_w_deg=5.66, fov_h_deg=3.86,
                 classes=frozenset({"HA", "OIII", "SII", "BB"}))
OSC_RIG = RigSpec(id=2, name="OSC 105mm", scale_arcsec=6.5, fov_w_deg=12.6, fov_h_deg=8.4,
                  classes=frozenset({"OSC"}), is_color=True)
LONG_RIG = RigSpec(id=3, name="Long focus", scale_arcsec=0.34, fov_w_deg=0.39, fov_h_deg=0.27,
                   classes=frozenset({"HA", "OIII", "SII", "BB"}))
BB_RIG = RigSpec(id=4, name="Mono 346mm BB", scale_arcsec=2.27, fov_w_deg=2.94, fov_h_deg=2.22,
                 classes=frozenset({"BB"}))


def make_inputs(pool=None, rows=(), rigs=(NB_RIG,), night=FULL_MOON_NIGHT, as_of=None, masters=None,
                horizon=FLAT, goal_rows=()):
    pool = pool or standard_pool()
    history = build_history(list(rows), as_of=as_of, masters=masters)
    goals = infer_goals(history, {c.key: c.kind for c in pool.candidates}, goal_rows, as_of)
    return EngineInputs(candidates=pool, history=history, site=SITE, horizon=horizon, rigs=list(rigs),
                        goals=goals, night=night)


def ngc7000_rows():
    """Active project: 11 nights, last imaged 32 days before the full-Moon night."""
    rows = []
    for d in range(11):
        rows.append(HistoryRow("NGC7000", date(2026, 8, 25 - d), "Ha", 1400.0, 1, False, 1))
    return rows
