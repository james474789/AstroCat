"""QA-35: LZW-compressed TIFFs need the imagecodecs package."""
import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")


def test_lzw_tiff_round_trips(tmp_path):
    pytest.importorskip("imagecodecs")
    data = (np.random.default_rng(0).random((32, 32)) * 255).astype("uint8")
    path = tmp_path / "lzw.tif"
    tifffile.imwrite(str(path), data, compression="lzw")
    assert (tifffile.imread(str(path)) == data).all()


def test_imagecodecs_is_a_requirement():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    assert "imagecodecs==" in text
