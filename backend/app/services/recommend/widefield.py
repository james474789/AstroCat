"""
Curated wide-field candidates (R1b, docs/design/R1b-rig-aware-picking.md §3).

Messier, NGC, Caldwell and Sharpless objects are mostly under a degree, so a
wide rig (a 105 mm lens on a DSLR, 20 x 14 deg) has almost nothing to frame.
These are hand-picked regions of a few degrees across, keyed `WF_<SLUG>`.
Pure data: no SQLAlchemy and no imports from the rest of the engine
(candidates.py turns each entry into a Candidate). Kinds are the candidates.KIND_*
strings; EMISSION only for fields dominated by HII gas, since it opens the
narrowband classes.

Centres and sizes (the region's major axis in arcmin) were checked against the
catalog rows of the member objects and the standard positions of the named
stars; `members` are pool keys (canonical target keys) inside the field. They
carry their own history, so a WF region never folds any.
"""

from typing import NamedTuple, Tuple

WF_PREFIX = "WF_"


class WideField(NamedTuple):
    key: str
    name: str
    ra_deg: float
    dec_deg: float
    size_arcmin: float
    kind: str
    members: Tuple[str, ...] = ()


def _wf(slug: str, name: str, ra: float, dec: float, size_deg: float, kind: str,
        members: Tuple[str, ...] = ()) -> WideField:
    return WideField(WF_PREFIX + slug, name, ra, dec, round(size_deg * 60.0, 1), kind, members)


WIDE_FIELDS: Tuple[WideField, ...] = (
    _wf("CYGNUS_SADR", "Cygnus: Sadr region", 305.6, 40.3, 8.0, "EMISSION", ("IC1318", "NGC6910", "NGC6888", "M29")),
    _wf("NORTH_AMERICA_PELICAN", "North America + Pelican + Deneb", 313.0, 44.4, 6.0, "EMISSION",
        ("NGC7000", "IC5070")),
    _wf("CYGNUS_MILKY_WAY", "Whole Cygnus Milky Way", 308.0, 38.5, 18.0, "EMISSION",
        ("NGC7000", "IC1318", "NGC6888", "NGC6960", "NGC6992")),
    _wf("HEART_SOUL_DOUBLE", "Heart, Soul and Double Cluster", 38.5, 60.0, 8.0, "EMISSION",
        ("IC1805", "IC1848", "NGC869", "NGC884")),
    _wf("CASSIOPEIA_MILKY_WAY", "Cassiopeia Milky Way", 5.0, 60.0, 16.0, "EMISSION",
        ("NGC7635", "NGC281", "M52", "NGC457")),
    _wf("CEPHEUS_IC1396", "Cepheus: IC 1396 and the Elephant's Trunk", 325.0, 58.0, 5.0, "EMISSION", ("IC1396",)),
    _wf("CEPHEUS_WIZARD_BUBBLE_CAVE", "Cepheus: Wizard, Bubble and Cave", 345.2, 60.3, 7.0, "EMISSION",
        ("NGC7380", "NGC7635", "SH2155")),
    _wf("CEPHEUS_IRIS_DUST", "Iris Nebula and Cepheus dust", 315.4, 68.2, 4.0, "REFLECTION", ("NGC7023",)),
    _wf("SHARK_LDN1235", "Shark Nebula (LDN 1235) region", 333.7, 73.4, 5.0, "REFLECTION"),
    _wf("ORION_BELT_SWORD", "Orion: Belt and Sword", 84.5, -3.6, 6.0, "EMISSION",
        ("M42", "NGC1977", "NGC2024", "IC434")),
    _wf("BARNARDS_LOOP", "Barnard's Loop", 84.0, -2.0, 12.0, "EMISSION", ("SH2276", "M42")),
    _wf("ORION_WHOLE", "Whole Orion", 84.0, -0.5, 18.0, "EMISSION", ("M42", "SH2276")),
    _wf("CALIFORNIA_PLEIADES", "California Nebula + Pleiades", 58.7, 30.25, 15.0, "OTHER", ("NGC1499", "M45")),
    _wf("TAURUS_DARK_CLOUDS", "Taurus dark clouds", 67.0, 24.5, 10.0, "OTHER"),
    _wf("HYADES_PLEIADES", "Hyades + Pleiades", 61.75, 20.0, 15.0, "CLUSTER", ("M45",)),
    _wf("AURIGA", "Auriga: Flaming Star, Tadpole, M36/37/38", 83.6, 33.6, 9.0, "EMISSION",
        ("IC405", "IC410", "M36", "M37", "M38")),
    _wf("ROSETTE_CONE", "Rosette + Cone", 99.0, 7.4, 8.0, "EMISSION", ("NGC2237", "NGC2264")),
    _wf("SEAGULL", "Seagull Nebula (IC 2177)", 106.2, -10.7, 4.0, "EMISSION", ("IC2177",)),
    _wf("ANDROMEDA_TRIANGULUM", "Andromeda + Triangulum", 17.1, 36.0, 16.0, "GALAXY", ("M31", "M33")),
    _wf("M81_M82_IFN", "M81 / M82 and the integrated flux nebula", 149.3, 69.3, 4.0, "OTHER", ("M81", "M82")),
    _wf("VIRGO_MARKARIAN", "Markarian's Chain and Virgo", 187.8, 12.6, 6.0, "GALAXY", ("M84", "M86", "M87")),
    _wf("EAGLE_SWAN_M24", "Eagle, Swan and M24", 274.8, -16.5, 8.0, "EMISSION", ("M16", "M17", "M24")),
    _wf("LAGOON_TRIFID", "Lagoon + Trifid", 270.75, -23.7, 5.0, "EMISSION", ("M8", "M20")),
    _wf("RHO_OPHIUCHI", "Rho Ophiuchi and Antares", 246.8, -24.5, 8.0, "REFLECTION", ("M4",)),
    _wf("MILKY_WAY_CORE", "Milky Way core", 268.0, -25.0, 20.0, "OTHER", ("M8", "M20", "M24")),
)
