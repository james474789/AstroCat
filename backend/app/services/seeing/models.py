"""
Regional weather-model coverage boxes and model selection (design §3.1).

This list holds model coverage areas only, never sites. Boxes are approximate outer bounds of each model's
domain; a point inside is offered to Open-Meteo as an extra model (the smallest box wins, being the
highest-resolution model). If Open-Meteo has no data there, the fetch retries with the global models only.
"""

from typing import List, NamedTuple, Optional, Tuple

GLOBAL_MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]
REFERENCE_MODEL = "ecmwf_ifs025"   # jet / shear levels come from here when a model lacks them (design §12)


class ModelBox(NamedTuple):
    model: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max

    @property
    def area(self) -> float:
        return (self.lat_max - self.lat_min) * (self.lon_max - self.lon_min)

    @property
    def centre(self) -> Tuple[float, float]:
        return (self.lat_min + self.lat_max) / 2.0, (self.lon_min + self.lon_max) / 2.0


REGIONAL_BOXES: List[ModelBox] = [
    ModelBox("ukmo_uk_deterministic_2km", 48.0, 61.5, -12.0, 5.0),
    ModelBox("icon_d2", 43.2, 58.1, -3.9, 20.3),
    ModelBox("meteofrance_arome_france_hd", 37.5, 55.4, -12.0, 16.0),
    ModelBox("gfs_hrrr", 21.0, 53.0, -134.0, -60.0),
]


def regional_model(lat: float, lon: float) -> Optional[str]:
    """The highest-resolution (smallest-box) regional model covering the point, else None."""
    hits = [b for b in REGIONAL_BOXES if b.contains(lat, lon)]
    if not hits:
        return None
    return min(hits, key=lambda b: b.area).model


def pick_models(lat: float, lon: float) -> Tuple[List[str], str]:
    """(models to request, primary). Regional model first and primary when present; ECMWF primary otherwise."""
    regional = regional_model(lat, lon)
    if regional:
        return [regional] + GLOBAL_MODELS, regional
    return list(GLOBAL_MODELS), REFERENCE_MODEL
