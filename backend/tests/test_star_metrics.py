"""
Q1 star metrics (docs/design/20260927-Q1-star-quality.md §4, §9): accuracy on
synthetic star fields with a known PSF, rejection rules, CFA handling and
file loading.
"""

import math

import numpy as np
import pytest

pytest.importorskip("sep")
pytest.importorskip("scipy")

from app.services.star_metrics import (  # noqa: E402
    ALGO_VERSION, MOFFAT_BETA, SkipMeasurement, is_cfa_header, load_luminance,
    measure, measure_array, superpixel,
)

GAUSS_HFR_PER_SIGMA = math.sqrt(2.0 * math.log(2.0))          # 1.1774
GAUSS_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))   # 2.3548


def _moffat_hfr(fwhm: float, beta: float = MOFFAT_BETA) -> float:
    alpha = fwhm / (2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0))
    # Enclosed flux of a Moffat: 1 - (1 + r^2/alpha^2)^(1-beta) = 0.5
    return alpha * math.sqrt(0.5 ** (1.0 / (1.0 - beta)) - 1.0)


def star_field(fwhm=4.0, n=250, shape=(1200, 1600), profile="moffat", ellipticity=0.0,
               theta=0.0, background=1000.0, noise=10.0, seed=1, gradient=True,
               flux_range=(2e4, 3e5), saturate=None, hot_pixels=0):
    """Linear frame of n stars with a known PSF on a (graded) sky with Gaussian noise."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = np.full(shape, background, dtype=np.float64)
    if gradient:
        img += np.linspace(0, background * 0.3, w)[None, :]
    major = fwhm
    minor = fwhm * math.sqrt(1.0 - ellipticity ** 2)
    ct, st = math.cos(theta), math.sin(theta)
    half = int(max(8, 6 * fwhm))
    xs = rng.uniform(40, w - 40, n)
    ys = rng.uniform(40, h - 40, n)
    fluxes = np.exp(rng.uniform(np.log(flux_range[0]), np.log(flux_range[1]), n))
    for x0, y0, flux in zip(xs, ys, fluxes):
        xi, yi = int(x0), int(y0)
        yy, xx = np.mgrid[yi - half: yi + half + 1, xi - half: xi + half + 1]
        dx, dy = xx - x0, yy - y0
        u = dx * ct + dy * st
        v = -dx * st + dy * ct
        if profile == "gaussian":
            su = major / GAUSS_FWHM_PER_SIGMA
            sv = minor / GAUSS_FWHM_PER_SIGMA
            psf = np.exp(-0.5 * ((u / su) ** 2 + (v / sv) ** 2))
        else:
            k = 2.0 * math.sqrt(2.0 ** (1.0 / MOFFAT_BETA) - 1.0)
            au, av = major / k, minor / k
            psf = (1.0 + (u / au) ** 2 + (v / av) ** 2) ** (-MOFFAT_BETA)
        psf /= psf.sum()
        y_lo, x_lo = max(yi - half, 0), max(xi - half, 0)
        y_hi, x_hi = min(yi + half + 1, h), min(xi + half + 1, w)
        img[y_lo:y_hi, x_lo:x_hi] += (flux * psf)[y_lo - (yi - half): y_hi - (yi - half),
                                                   x_lo - (xi - half): x_hi - (xi - half)]
    img += rng.normal(0, noise, shape)
    for _ in range(hot_pixels):
        img[rng.integers(0, h), rng.integers(0, w)] = background + 20000
    if saturate is not None:
        img = np.minimum(img, saturate)
    return img.astype(np.float32)


class TestAccuracy:
    @pytest.mark.parametrize("fwhm", [2.5, 4.0, 6.0])
    def test_moffat_fwhm_and_hfr_within_5pct(self, fwhm):
        m = measure_array(star_field(fwhm=fwhm))
        assert m.status == "OK"
        assert m.details["fwhm_method"] == "MOFFAT"
        assert m.fwhm_px == pytest.approx(fwhm, rel=0.05)
        assert m.hfr_px == pytest.approx(_moffat_hfr(fwhm), rel=0.05)
        assert m.eccentricity < 0.35
        assert m.star_count > 100

    def test_gaussian_stars_hfr(self):
        sigma = 1.8
        m = measure_array(star_field(fwhm=sigma * GAUSS_FWHM_PER_SIGMA, profile="gaussian"))
        assert m.status == "OK"
        assert m.hfr_px == pytest.approx(sigma * GAUSS_HFR_PER_SIGMA, rel=0.05)
        # A beta=4 Moffat on Gaussian stars still lands close to the true FWHM.
        assert m.fwhm_px == pytest.approx(sigma * GAUSS_FWHM_PER_SIGMA, rel=0.10)

    def test_elongated_stars_eccentricity_and_angle(self):
        m = measure_array(star_field(fwhm=5.0, ellipticity=0.7, theta=math.radians(30)))
        assert m.status == "OK"
        assert m.eccentricity == pytest.approx(0.7, abs=0.05)
        assert m.details["theta_deg"] == pytest.approx(30, abs=5)

    def test_large_frame_uses_binned_detection_without_bias(self):
        m = measure_array(star_field(fwhm=3.0, shape=(3000, 3000), n=800, seed=12))
        assert m.details["detect_bin"] == 2
        assert m.fwhm_px == pytest.approx(3.0, rel=0.05)
        assert m.hfr_px == pytest.approx(_moffat_hfr(3.0), rel=0.05)

    def test_larger_stars_measure_larger(self):
        sharp = measure_array(star_field(fwhm=3.0, seed=2))
        soft = measure_array(star_field(fwhm=4.5, seed=2))
        assert soft.fwhm_px > sharp.fwhm_px * 1.3
        assert soft.hfr_px > sharp.hfr_px * 1.3


class TestRejection:
    def test_saturated_stars_are_rejected_and_do_not_bias(self):
        img = star_field(fwhm=4.0, flux_range=(2e4, 3e6), saturate=60000.0, seed=3)
        m = measure_array(img, saturation=65535.0)
        assert m.status == "OK"
        assert m.details["saturated_rejected"] > 0
        assert m.fwhm_px == pytest.approx(4.0, rel=0.06)

    def test_hot_pixels_are_not_stars(self):
        m = measure_array(star_field(fwhm=4.0, hot_pixels=300, seed=4))
        assert m.status == "OK"
        assert m.fwhm_px == pytest.approx(4.0, rel=0.05)

    def test_warm_pixel_pairs_do_not_hijack_fwhm_on_oversampled_data(self):
        # Real case (NGC2392, EdgeHD at 0.34"/px, uncalibrated 300 s Ha): soft
        # 8 px stars plus bright 2-pixel warm-pixel clusters whose peaks beat
        # the stars'. Fits picked by peak landed on the warm pixels (FWHM 1.2 px
        # vs HFR 4.4 px). Candidates must be chosen by flux and sharp outliers dropped.
        rng = np.random.default_rng(21)
        img = star_field(fwhm=8.0, n=300, shape=(1500, 1500), flux_range=(5e4, 6e5), seed=21)
        for _ in range(400):
            y, x = rng.integers(20, 1480, 2)
            img[y, x] += 9000
            img[y, x + 1] += 6000
        m = measure_array(img)
        assert m.status == "OK"
        assert m.fwhm_px == pytest.approx(8.0, rel=0.08)
        assert m.hfr_px == pytest.approx(_moffat_hfr(8.0), rel=0.08)
        assert m.star_count < 400   # warm pixels are not counted as stars

    def test_blank_frame_is_no_stars(self):
        rng = np.random.default_rng(5)
        blank = (1000 + rng.normal(0, 10, (800, 800))).astype(np.float32)
        m = measure_array(blank)
        assert m.status == "NO_STARS"
        assert m.hfr_px is None and m.fwhm_px is None
        assert m.star_count is not None and m.star_count < 10

    def test_undersampled_falls_back_or_flags(self):
        m = measure_array(star_field(fwhm=1.2, seed=6))
        assert m.status == "OK"
        assert m.details["undersampled"] is True


class TestCFA:
    def test_superpixel_sums_2x2(self):
        d = np.arange(16, dtype=np.float32).reshape(4, 4)
        assert superpixel(d).tolist() == [[0 + 1 + 4 + 5, 2 + 3 + 6 + 7], [8 + 9 + 12 + 13, 10 + 11 + 14 + 15]]

    def test_cfa_results_are_in_native_pixels(self):
        # A mono field measured as a Bayer mosaic must report the same sizes.
        img = star_field(fwhm=6.0, shape=(1600, 2000), n=300, seed=7)
        mono = measure_array(img)
        cfa = measure_array(img, cfa_factor=2)
        assert cfa.status == "OK"
        assert cfa.details["cfa_factor"] == 2
        assert cfa.fwhm_px == pytest.approx(mono.fwhm_px, rel=0.10)
        assert cfa.hfr_px == pytest.approx(mono.hfr_px, rel=0.10)

    @pytest.mark.parametrize("value,expected", [
        ("RGGB", True), ("gbrg", True), (" BGGR ", True), ("MONO", False), (None, False), ("", False),
    ])
    def test_is_cfa_header(self, value, expected):
        assert is_cfa_header({"BAYERPAT": value}) is expected


class TestLoading:
    def test_jpg_is_skipped(self, tmp_path):
        m = measure(str(tmp_path / "x.jpg"), "JPG")
        assert m.status == "SKIPPED"
        assert m.details["reason"] == "NONLINEAR_FORMAT"
        assert m.details["algo_version"] == ALGO_VERSION

    def test_fits_uint16_with_bzero_and_bayer(self, tmp_path):
        from astropy.io import fits
        img = star_field(fwhm=5.0, shape=(800, 1000), n=150, seed=8)
        data = np.clip(img, 0, 65535).astype(np.uint16)
        hdu = fits.PrimaryHDU(data)   # astropy writes uint16 as int16 + BZERO 32768
        hdu.header["BAYERPAT"] = "RGGB"
        path = tmp_path / "osc.fits"
        hdu.writeto(path)
        arr, cfa_factor, saturation = load_luminance(str(path), "FITS", {})
        assert cfa_factor == 2
        assert arr.dtype == np.float32 and arr.shape == (800, 1000)
        assert saturation == 65535.0
        # BZERO applied: values round-trip as unsigned, not shifted by -32768.
        assert np.array_equal(arr, data.astype(np.float32))

    def test_fits_rgb_cube_uses_one_plane(self, tmp_path):
        from astropy.io import fits
        img = star_field(fwhm=4.0, shape=(400, 500), n=60, seed=9)
        cube = np.stack([img * 0.5, img, img * 0.7]).astype(np.float32)
        path = tmp_path / "rgb.fits"
        fits.PrimaryHDU(cube).writeto(path)
        arr, cfa_factor, _ = load_luminance(str(path), "FITS", {"BAYERPAT": "RGGB"})
        assert cfa_factor == 1
        assert np.allclose(arr, img, atol=1e-3)

    def test_measure_fits_end_to_end(self, tmp_path):
        from astropy.io import fits
        img = star_field(fwhm=4.0, seed=10)
        path = tmp_path / "mono.fits"
        fits.PrimaryHDU(img).writeto(path)
        m = measure(str(path), "FITS", None)
        assert m.status == "OK"
        assert m.fwhm_px == pytest.approx(4.0, rel=0.05)

    def test_unsupported_format_is_skipped(self):
        with pytest.raises(SkipMeasurement):
            load_luminance("x.bmp", "BMP", None)
