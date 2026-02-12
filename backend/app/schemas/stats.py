from pydantic import BaseModel

class StatsOverview(BaseModel):
    total_images: int
    total_plate_solved: int
    total_messier_matches: int
    total_ngc_matches: int
    total_exposure_hours: float
    storage_used_bytes: int
