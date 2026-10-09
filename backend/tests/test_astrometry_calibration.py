from app.tasks.astrometry import calibration_has_solution


def test_calibration_with_centre_is_usable():
    assert calibration_has_solution({"ra": 308.5, "dec": 42.9, "radius": 1.2})
    assert calibration_has_solution({"ra": "308.5", "dec": "42.9"})


def test_empty_or_partial_calibration_is_rejected():
    assert not calibration_has_solution({})
    assert not calibration_has_solution(None)
    assert not calibration_has_solution({"ra": None, "dec": None})
    assert not calibration_has_solution({"ra": 308.5})
    assert not calibration_has_solution({"ra": float("nan"), "dec": 1.0})
