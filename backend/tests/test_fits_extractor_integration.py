"""
Integration tests: FITS extractor survives genuinely malformed header cards.

The card used here is copied verbatim from a real AllSkyCamera capture that
poisoned the index loop (the SharpCap/QHY driver writes an unquoted Infinity
value that astropy cannot parse):

    CCD-TEMP=             Infinity /

Reading that card's value raises astropy.io.fits.verify.VerifyError, which
used to abort process_image before the database insert - so the same ~10k
files were re-detected as "new" on every scan. These tests pin down the
fault-tolerant behaviour: extract() must not raise, must return all readable
metadata, and must produce a JSON-safe raw_header dict.

The malformed cards are spliced into the raw file bytes (exactly how the
camera wrote them) because astropy itself refuses to serialize them.
"""

import json
from datetime import datetime, date

import numpy as np
import pytest
from astropy.io import fits

from app.extractors.fits_extractor import FITSExtractor
from app.tasks.indexer import sanitize_metadata

# Exact card as written by the all-sky camera software
REAL_BAD_TEMP_CARD = "CCD-TEMP=             Infinity /"


def _make_fits_with_cards(tmp_path, raw_card_lines):
    """
    Create a valid FITS file with placeholder cards, then splice the malformed
    card images over the placeholder slots in the raw file bytes (mirroring
    how the camera wrote them).
    """
    path = tmp_path / "broken.fits"
    data = np.zeros((4, 4), dtype=np.float32)
    hdu = fits.PrimaryHDU(data=data)
    header = hdu.header
    header["OBJECT"] = "M31"
    header["TELESCOP"] = "AllSkyCam"
    header["EXPTIME"] = 30.0
    header["DATE-OBS"] = "2026-08-29T12:00:00"
    for line in raw_card_lines:
        keyword = line.split("=")[0].strip()
        header[keyword] = 0  # valid placeholder, spliced over below
    hdu.writeto(path)

    raw = bytearray(path.read_bytes())
    for line in raw_card_lines:
        keyword = line.split("=")[0].strip()
        needle = keyword.encode("ascii").ljust(8)
        idx = raw.find(needle)
        assert idx != -1, "placeholder card not found for %s" % keyword
        raw[idx:idx + 80] = line.ljust(80).encode("ascii")[:80]
    path.write_bytes(bytes(raw))
    return path


class TestMalformedRealFITSFile:
    """extract() against real FITS files with unparsable cards."""

    def test_real_infinity_temp_card_does_not_abort_extraction(self, tmp_path):
        path = _make_fits_with_cards(tmp_path, [REAL_BAD_TEMP_CARD])

        # Sanity check: astropy itself refuses to parse this card
        with fits.open(path) as hdul:
            with pytest.raises(Exception):
                hdul[0].header.get("CCD-TEMP")

        metadata = FITSExtractor(str(path)).extract()

        assert metadata["object_name"] == "M31"
        assert metadata["telescope_name"] == "AllSkyCam"
        assert metadata["exposure_time_seconds"] == 30.0
        assert metadata["width_pixels"] == 4
        assert metadata["height_pixels"] == 4
        # The unparsable temperature is reported as missing, not fatal
        assert metadata["temperature_celsius"] is None
        # The raw value is salvaged as text for the raw_header viewer
        assert metadata["raw_header"]["CCD-TEMP"] == "Infinity"

    def test_raw_header_is_json_safe_with_malformed_cards(self, tmp_path):
        path = _make_fits_with_cards(
            tmp_path,
            [REAL_BAD_TEMP_CARD, "GUIDEST = "],
        )
        metadata = FITSExtractor(str(path)).extract()

        raw_header = metadata["raw_header"]
        assert isinstance(raw_header, dict)
        # Must be JSON-serializable (JSONB column requirement)
        json.dumps(raw_header)
        assert raw_header["OBJECT"] == "M31"
        assert raw_header["CCD-TEMP"] == "Infinity"
        assert raw_header["GUIDEST"] is None

    def test_sanitize_metadata_normalizes_nan_and_datetime(self):
        cleaned = sanitize_metadata({
            "nan": float("nan"),
            "inf": float("inf"),
            "when": datetime(2026, 1, 2, 3, 4, 5),
            "nested": [{"day": date(2026, 9, 1)}],
            "text": "bad\x00value",
        })
        assert cleaned["nan"] is None
        assert cleaned["inf"] is None
        assert cleaned["when"] == "2026-01-02T03:04:05"
        assert cleaned["nested"] == [{"day": "2026-09-01"}]
        assert cleaned["text"] == "badvalue"

    def test_fits_with_blank_value_card_is_json_safe(self, tmp_path):
        # Blank cards (e.g. "KEYWORD = ") parse to astropy Undefined, which
        # is not JSON-serializable and previously could poison the record.
        path = _make_fits_with_cards(tmp_path, ["SOMEFLAG = "])
        metadata = FITSExtractor(str(path)).extract()
        json.dumps(metadata["raw_header"])


class TestQHY5LIIExposureUnits:
    """
    One QHY5LII-C capture software never writes EXPTIME and writes the
    non-standard EXPOSURE in milliseconds, not seconds - uniquely
    identifiable by its "Presion Time" (sic) HIERARCH card. A real NGC6888
    sub from this software inflated a 60s exposure to 60000 stored seconds,
    which summed 27 subs to ~466h of integration time instead of ~0.45h.
    See app/scripts/repair_qhy5lii_exposure.py for the matching data repair.
    """

    def _make_qhy5lii_fits(self, tmp_path, exposure_ms, filename="qhy.fits"):
        path = tmp_path / filename
        data = np.zeros((4, 4), dtype=np.float32)
        hdu = fits.PrimaryHDU(data=data)
        header = hdu.header
        header["CAMERA"] = "QHY5LII-C-6177da10f33c7354"
        header["EXPOSURE"] = exposure_ms
        header["HIERARCH Presion Time"] = "21-10-5-751"
        hdu.writeto(path)
        return path

    def test_exposure_is_converted_from_milliseconds(self, tmp_path):
        path = self._make_qhy5lii_fits(tmp_path, 60000)
        metadata = FITSExtractor(str(path)).extract()
        assert metadata["exposure_time_seconds"] == 60.0

    def test_other_cameras_exposure_in_seconds_is_untouched(self, tmp_path):
        # No "Presion Time" fingerprint -> EXPOSURE is trusted as seconds,
        # exactly as before this fix.
        path = tmp_path / "other.fits"
        data = np.zeros((4, 4), dtype=np.float32)
        hdu = fits.PrimaryHDU(data=data)
        header = hdu.header
        header["CAMERA"] = "Canon EOS 80D"
        header["EXPOSURE"] = 300.0
        hdu.writeto(path)

        metadata = FITSExtractor(str(path)).extract()
        assert metadata["exposure_time_seconds"] == 300.0

    def test_exptime_present_is_never_rescaled_even_with_fingerprint(self, tmp_path):
        # Defensive: if this software ever does write a standard EXPTIME,
        # trust it over EXPOSURE and never rescale it.
        path = tmp_path / "qhy_with_exptime.fits"
        data = np.zeros((4, 4), dtype=np.float32)
        hdu = fits.PrimaryHDU(data=data)
        header = hdu.header
        header["EXPTIME"] = 60.0
        header["EXPOSURE"] = 60000
        header["HIERARCH Presion Time"] = "21-10-5-751"
        hdu.writeto(path)

        metadata = FITSExtractor(str(path)).extract()
        assert metadata["exposure_time_seconds"] == 60.0
