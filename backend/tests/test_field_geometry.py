import pytest

from app.utils.field_geometry import effective_field_radius, field_radius_from_scale


def test_radius_from_scale_half_diagonal():
    # 3000x4000 px -> diagonal 5000 px; 3.6"/px -> 5000 * 3.6 / 3600 / 2 = 2.5 deg
    assert field_radius_from_scale(3000, 4000, 3.6) == pytest.approx(2.5)


@pytest.mark.parametrize("w,h,scale", [(None, 100, 1.0), (100, 0, 1.0), (100, 100, None), (100, 100, 0), (100, 100, -1.0)])
def test_radius_from_scale_missing_inputs(w, h, scale):
    assert field_radius_from_scale(w, h, scale) is None


def test_effective_radius_keeps_positive_value():
    assert effective_field_radius(1.2, 3000, 4000, 3.6) == 1.2


@pytest.mark.parametrize("radius", [None, 0, 0.0])
def test_effective_radius_derives_when_missing_or_zero(radius):
    assert effective_field_radius(radius, 3000, 4000, 3.6) == pytest.approx(2.5)


def test_effective_radius_none_when_underivable():
    assert effective_field_radius(0, None, None, 3.6) is None
