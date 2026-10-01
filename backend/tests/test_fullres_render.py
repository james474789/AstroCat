"""
V1 full-resolution viewer: rendering (stretch, debayer, orientation).
docs/design/20261001-V1-full-resolution-viewer.md §4.1 and §8.
"""

import itertools

import numpy as np
import pytest
from PIL import Image as PILImage

from app.config import settings
from app.services.fullres import render
from app.services.thumbnails import ThumbnailGenerator
from _fullres_helpers import make_image, scene, write_fits

try:
    import cv2  # noqa: F401
except ImportError:  # pragma: no cover
    cv2 = None

needs_cv2 = pytest.mark.skipif(cv2 is None, reason="OpenCV not installed")


# ---------------------------------------------------------------------------
# Stretch
# ---------------------------------------------------------------------------

def _legacy_apply_stf_stretch(data, target_bg=0.25, shadows_clip=-1.25):
    """The pre-refactor implementation, kept to prove the stf_params/stf_curve split is output-identical."""
    data = np.nan_to_num(data.astype(np.float32, copy=False))
    d_min = np.min(data)
    d_max = np.max(data)
    if d_max <= d_min:
        return np.zeros_like(data, dtype=np.uint8)
    data = (data - d_min) / (d_max - d_min)
    median = np.median(data)
    mad = np.median(np.abs(data - median))
    c0 = max(0.0, median + shadows_clip * mad)
    data = (np.clip(data, c0, 1.0) - c0) / (1.0 - c0)
    median_new = np.median(data)
    denominator = median_new + target_bg - 2 * median_new * target_bg
    midpoint = (median_new * (1 - target_bg) / denominator
                if denominator and 0 < median_new < 1 and median_new != target_bg else 0.5)
    if midpoint != 0.5:
        with np.errstate(divide="ignore", invalid="ignore"):
            data = ((midpoint - 1) * data) / ((2 * midpoint - 1) * data - midpoint)
    return (np.clip(data, 0, 1) * 255).astype(np.uint8)


@pytest.mark.parametrize("data", [
    scene(),
    scene().astype(np.float32) / 65535.0,
    np.full((20, 30), 7, dtype=np.uint16),                       # flat frame
    np.random.default_rng(1).normal(0.1, 0.02, (50, 60, 3)).astype(np.float32),
    np.where(np.random.default_rng(2).random((40, 40)) < 0.01, np.nan, 0.3).astype(np.float32),
])
def test_stf_refactor_is_output_identical(data):
    np.testing.assert_array_equal(ThumbnailGenerator.apply_stf_stretch(data), _legacy_apply_stf_stretch(data))


def test_stf_with_bounds_fits_a_sample_to_the_whole_frame():
    data = scene().astype(np.float32)
    sample = data[::4, ::4]
    params = ThumbnailGenerator.stf_params(sample, bounds=(np.float32(data.min()), np.float32(data.max())))
    assert params["d_min"] == data.min() and params["d_max"] == data.max()
    assert ThumbnailGenerator.stf_curve(data, params).shape == data.shape


def test_uint16_lut_path_matches_apply_stf_stretch(tmp_path):
    data = scene()
    path = write_fits(tmp_path / "mono.fits", data)
    result = render.render_full(make_image(path), "auto")
    assert result.array.shape == data.shape and result.scale == 1
    expected = ThumbnailGenerator.apply_stf_stretch(data)
    assert np.abs(result.array.astype(int) - expected.astype(int)).max() <= 1


def test_signed_big_endian_fits_uses_the_lut_path(tmp_path):
    data = (scene().astype(np.int32) - 20000).astype(np.int16)    # BITPIX 16, no BZERO: big-endian int16
    path = write_fits(tmp_path / "signed.fits", data)
    result = render.render_full(make_image(path), "auto")
    expected = ThumbnailGenerator.apply_stf_stretch(data)
    assert np.abs(result.array.astype(int) - expected.astype(int)).max() <= 1


def test_float_row_blocks_equal_whole_array(tmp_path):
    rng = np.random.default_rng(3)
    data = (rng.gamma(2.0, 0.02, (700, 300))).astype(np.float32)   # > 256 rows, so several blocks
    path = write_fits(tmp_path / "float.fits", data)
    result = render.render_full(make_image(path), "auto")
    np.testing.assert_array_equal(result.array, ThumbnailGenerator.apply_stf_stretch(data))


def test_linear_preset_is_min_max(tmp_path):
    data = scene()
    path = write_fits(tmp_path / "mono.fits", data)
    result = render.render_full(make_image(path), "linear")
    expected = ThumbnailGenerator._normalize(data, apply_stf=False)
    assert np.abs(result.array.astype(int) - expected.astype(int)).max() <= 1


def test_unlinked_equalises_channel_medians(tmp_path):
    import tifffile
    rng = np.random.default_rng(4)
    base = np.array([1000, 3000, 7000], dtype=np.float32)       # strong colour cast
    data = (base + rng.gamma(2.0, 60.0, (120, 160, 3))).astype(np.uint16)
    path = tmp_path / "cast.tif"
    tifffile.imwrite(str(path), data)
    image = make_image(path)
    medians = lambda a: [np.median(a[..., c]) / 255.0 for c in range(3)]  # noqa: E731
    unlinked = medians(render.render_full(image, "unlinked").array)
    linked = medians(render.render_full(image, "auto").array)
    assert all(abs(m - 0.25) < 0.03 for m in unlinked)
    assert max(linked) - min(linked) > 0.1


def test_unlinked_is_rejected_for_mono(tmp_path):
    path = write_fits(tmp_path / "mono.fits", scene())
    with pytest.raises(ValueError):
        render.render_full(make_image(path), "unlinked")


def test_size_guard_downsamples_by_an_integer_factor(tmp_path, monkeypatch):
    path = write_fits(tmp_path / "mono.fits", scene(240, 320))
    monkeypatch.setattr(settings, "fullres_max_megapixels", 0.02)   # 20,000 px cap for a 76,800 px frame
    result = render.render_full(make_image(path), "linear")
    assert result.scale == 2
    assert result.array.shape == (120, 160)
    assert (result.native_width, result.native_height) == (320, 240)
    assert any("1/2" in n for n in result.notes)


def test_png_passthrough_keeps_pixels(tmp_path):
    rgb = np.random.default_rng(5).integers(0, 256, (30, 40, 3), dtype=np.uint8)
    path = tmp_path / "p.png"
    PILImage.fromarray(rgb).save(path)
    result = render.render_full(make_image(path), "linear")
    np.testing.assert_array_equal(result.array, rgb)


def test_cube_with_three_planes_is_rgb(tmp_path):
    cube = np.stack([scene(60, 80), scene(60, 80) // 2, scene(60, 80) // 4])
    path = write_fits(tmp_path / "cube.fits", cube)
    result = render.render_full(make_image(path), "linear")
    assert result.array.shape == (60, 80, 3)


# ---------------------------------------------------------------------------
# Debayer
# ---------------------------------------------------------------------------

CHANNEL = {"R": 0, "G": 1, "B": 2}
BAND = 16


def _bands_scene(height=16, width=3 * BAND, level=4000):
    """Pure red | pure green | pure blue vertical bands, as an (H, W, 3) truth image."""
    truth = np.zeros((height, width, 3), dtype=np.uint16)
    for band in range(3):
        truth[:, band * BAND:(band + 1) * BAND, band] = level
    return truth


def _mosaic(truth, pattern, x_off=0, y_off=0, bottom_up=False):
    """Sample `truth` through a Bayer array, defined independently of render.effective_bayer_pattern.

    The sensor's colour at stored array position (i, j): BAYERPAT's grid indexed by the row
    (counted from the other end when the file is bottom-up, which is even-height here) and column,
    each shifted by the X/Y offset.
    """
    height, width, _ = truth.shape
    grid = [[pattern[0], pattern[1]], [pattern[2], pattern[3]]]
    cfa = np.zeros((height, width), dtype=np.uint16)
    for i in range(height):
        row = (height - 1 - i + y_off) if bottom_up else (i + y_off)
        for j in range(width):
            cfa[i, j] = truth[i, j, CHANNEL[grid[row % 2][(j + x_off) % 2]]]
    return cfa


def _interior_colours(rgb):
    """One (R, G, B) per band, sampled well away from band edges and the image border."""
    return [rgb[4:-4, band * BAND + 4:(band + 1) * BAND - 4].reshape(-1, 3) for band in range(3)]


@needs_cv2
@pytest.mark.parametrize("pattern", render.BAYER_PATTERNS)
def test_opencv_code_mapping_gives_pure_colours(pattern):
    truth = _bands_scene()
    rgb = render.debayer(_mosaic(truth, pattern), pattern)
    for band, pixels in enumerate(_interior_colours(rgb)):
        expected = np.zeros(3)
        expected[band] = 4000
        assert (pixels == expected).all(), (pattern, band)


@needs_cv2
@pytest.mark.parametrize("pattern,x_off,y_off,bottom_up",
                         list(itertools.product(render.BAYER_PATTERNS, (0, 1), (0, 1), (False, True))))
def test_header_driven_debayer_for_every_pattern_offset_and_row_order(tmp_path, pattern, x_off, y_off, bottom_up):
    truth = _bands_scene()
    cfa = _mosaic(truth, pattern, x_off, y_off, bottom_up)
    path = write_fits(tmp_path / "osc.fits", cfa, BAYERPAT=pattern, XBAYROFF=x_off, YBAYROFF=y_off,
                      ROWORDER="BOTTOM-UP" if bottom_up else "TOP-DOWN")
    result = render.render_full(make_image(path), "linear")
    assert result.array.shape == (16, 48, 3)
    assert any("debayered" in n for n in result.notes)
    for band, pixels in enumerate(_interior_colours(result.array)):
        expected = np.zeros(3)
        expected[band] = 255
        assert (pixels == expected).all(), (pattern, x_off, y_off, bottom_up, band)


@needs_cv2
def test_odd_offsets_and_bottom_up_combine():
    # RGGB shifted one column, bottom-up: rows swap and columns swap -> BGGR... verify the arithmetic
    assert render.effective_bayer_pattern("RGGB", 1, 0, False) == "GRBG"
    assert render.effective_bayer_pattern("RGGB", 0, 1, False) == "GBRG"
    assert render.effective_bayer_pattern("RGGB", 0, 0, True) == "GBRG"
    assert render.effective_bayer_pattern("RGGB", 1, 1, True) == "GRBG"
    assert render.effective_bayer_pattern("BGGR", 2, 2, False) == "BGGR"


def test_parse_bayer_header():
    assert render.parse_bayer_header({"BAYERPAT": " rggb ", "ROWORDER": "BOTTOM-UP", "XBAYROFF": "1"}) == \
        ("RGGB", 1, 0, True)
    assert render.parse_bayer_header({"BAYERPAT": "None"}) is None
    assert render.parse_bayer_header({"COLORTYP": "GBRG"})[0] == "GBRG"
    assert render.parse_bayer_header({}) is None


@needs_cv2
def test_debayer_kill_switch(tmp_path, monkeypatch):
    cfa = _mosaic(_bands_scene(), "RGGB")
    path = write_fits(tmp_path / "osc.fits", cfa, BAYERPAT="RGGB")
    monkeypatch.setattr(settings, "fullres_debayer", False)
    assert render.render_full(make_image(path), "linear").array.ndim == 2
    assert render.probe_source(make_image(path)).colour is False
    monkeypatch.setattr(settings, "fullres_debayer", True)
    assert render.render_full(make_image(path), "linear").array.ndim == 3
    assert render.probe_source(make_image(path)).colour is True


@needs_cv2
def test_bayer_unlinked_is_valid_after_debayer(tmp_path):
    cfa = _mosaic(_bands_scene(), "BGGR")
    path = write_fits(tmp_path / "osc.fits", cfa, BAYERPAT="BGGR")
    assert render.render_full(make_image(path), "unlinked").array.shape == (16, 48, 3)


# ---------------------------------------------------------------------------
# Orientation: the full-res render must match the thumbnail (no flips)
# ---------------------------------------------------------------------------

def _write_tiff(path, data):
    import tifffile
    tifffile.imwrite(str(path), data)


def _write_xisf(path, data):
    import xisf
    xisf.XISF.write(str(path), data[..., None])


def _corr(a, b):
    return float(np.corrcoef(a.ravel().astype(float), b.ravel().astype(float))[0, 1])


@pytest.mark.parametrize("kind", ["fits", "tiff", "xisf"])
def test_render_matches_thumbnail_orientation(tmp_path, kind):
    data = scene(480, 640)
    path = tmp_path / f"scene.{ {'fits': 'fits', 'tiff': 'tif', 'xisf': 'xisf'}[kind] }"
    {"fits": write_fits, "tiff": _write_tiff, "xisf": _write_xisf}[kind](path, data)

    thumb = ThumbnailGenerator.load_source_image(str(path), is_subframe=True, apply_stf=True, target_size=(128, 128))
    assert thumb is not None
    thumb_grey = np.asarray(thumb.convert("L"))

    result = render.render_full(make_image(path), "auto")
    assert result.array.shape[:2] == (480, 640)
    small = np.asarray(PILImage.fromarray(result.array).convert("L").resize(thumb.size, PILImage.Resampling.BOX))
    assert _corr(small, thumb_grey) > 0.98
    # the scene is asymmetric, so a flipped render must not pass
    assert _corr(small[::-1], thumb_grey) < 0.9
    assert _corr(small[:, ::-1], thumb_grey) < 0.9


# ---------------------------------------------------------------------------
# XISF planar block reader
# ---------------------------------------------------------------------------

def _rgb_scene(height=700, width=300):
    rng = np.random.default_rng(6)
    return np.stack([scene(height, width, seed=s) // (s + 1) + rng.integers(0, 50, (height, width), dtype=np.uint16)
                     for s in range(3)], axis=-1).astype(np.uint16)


def test_xisf_planar_reader_matches_in_memory_stretch_across_blocks(tmp_path):
    import xisf
    data = _rgb_scene()                                     # 700 rows: three 256-row blocks
    path = tmp_path / "rgb.xisf"
    xisf.XISF.write(str(path), data)
    frame_meta = xisf.XISF(str(path)).get_images_metadata()[0]
    assert frame_meta["location"][0] == "attachment" and "compression" not in frame_meta   # the PlanarFile path
    result = render.render_full(make_image(path), "auto")
    assert result.array.shape == data.shape
    assert np.abs(result.array.astype(int) - ThumbnailGenerator.apply_stf_stretch(data).astype(int)).max() <= 1


def test_xisf_compressed_falls_back_to_a_full_read(tmp_path):
    import xisf
    data = _rgb_scene(120, 90)
    path = tmp_path / "rgbz.xisf"
    xisf.XISF.write(str(path), data, codec="zlib")
    assert "compression" in xisf.XISF(str(path)).get_images_metadata()[0]
    result = render.render_full(make_image(path), "linear")
    assert result.array.shape == data.shape


def test_xisf_mono_cfa_is_debayered_from_header_keywords(tmp_path):
    import xisf
    cfa = _mosaic(_bands_scene(), "GRBG")
    path = tmp_path / "cfa.xisf"
    xisf.XISF.write(str(path), cfa[..., None])
    result = render.render_full(make_image(path, raw_header={"BAYERPAT": "GRBG"}), "linear")
    assert result.array.shape == (16, 48, 3)
    for band, pixels in enumerate(_interior_colours(result.array)):
        expected = np.zeros(3)
        expected[band] = 255
        assert (pixels == expected).all(), band


def test_planar_file_short_reads_and_step(tmp_path):
    path = tmp_path / "planes.bin"
    data = np.arange(3 * 10 * 8, dtype=np.uint16).reshape(3, 10, 8)
    path.write_bytes(b"HDR!" + data.tobytes())
    reader = render.PlanarFile(str(path), "<u2", (3, 10, 8), 4, 3)
    np.testing.assert_array_equal(reader.rows(2, 9, 3), np.moveaxis(data[:, 2:9:3, ::3], 0, -1))
    mono = render.PlanarFile(str(path), "<u2", (3, 10, 8), 4, 1)
    np.testing.assert_array_equal(mono.rows(0, 10), data[0])


def test_xisf_over_the_ram_limit_uses_block_reads(tmp_path, monkeypatch):
    import xisf
    data = _rgb_scene()
    path = tmp_path / "big.xisf"
    xisf.XISF.write(str(path), data)
    monkeypatch.setattr(render, "RAM_LOAD_LIMIT_BYTES", 0)
    result = render.render_full(make_image(path), "auto")
    assert np.abs(result.array.astype(int) - ThumbnailGenerator.apply_stf_stretch(data).astype(int)).max() <= 1
