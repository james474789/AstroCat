"""QA-44: a truncated FITS file must fail extraction so the indexer reports it."""
import numpy as np
import pytest
from astropy.io import fits

from app.extractors.fits_extractor import FITSExtractor


def _write(path):
    fits.PrimaryHDU(np.zeros((200, 300), dtype="uint16")).writeto(path)


def test_complete_file_extracts(tmp_path):
    f = tmp_path / "ok.fits"
    _write(f)
    assert FITSExtractor(str(f)).extract()["width_pixels"] == 300


def test_truncated_file_raises(tmp_path):
    f = tmp_path / "bad.fits"
    _write(f)
    f.write_bytes(f.read_bytes()[:3000])
    with pytest.raises(ValueError, match="Truncated FITS"):
        FITSExtractor(str(f)).extract()
