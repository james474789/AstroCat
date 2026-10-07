"""
Q1 star quality pipeline (docs/design/20260927-Q1-star-quality.md §5): eligibility,
re-measure rules, applying results (incl. the capture-software hint
fallback), the sweeper's throttling, and hint parsing.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.models.image import FrameType, ImageSubtype
from app.services.star_metrics import ALGO_VERSION, StarMetrics
from app.tasks import quality
from app.tasks.quality import apply_result, is_eligible, needs_measurement, sweep_clause
from app.utils.star_metric_hints import extract_hints

MTIME = datetime(2026, 9, 26, 22, 14, 3)


def _image(**overrides):
    base = dict(
        id=1, frame_type=FrameType.LIGHT, subtype=ImageSubtype.SUB_FRAME,
        star_metrics_status=None, star_metrics_version=None, star_metrics=None,
        star_metrics_at=None, file_last_modified=MTIME,
        hfr_px=None, fwhm_px=None, eccentricity=None, star_count=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _measured(**overrides):
    fields = dict(star_metrics_status="OK", star_metrics_version=ALGO_VERSION,
                  star_metrics={"file_mtime": MTIME.isoformat()})
    fields.update(overrides)
    return _image(**fields)


class TestEligibility:
    @pytest.mark.parametrize("frame_type,subtype,expected", [
        (FrameType.LIGHT, ImageSubtype.SUB_FRAME, True),
        (FrameType.LIGHT, ImageSubtype.INTEGRATION_MASTER, True),
        (FrameType.LIGHT, ImageSubtype.PLANETARY, False),
        (FrameType.LIGHT, ImageSubtype.ALLSKY, False),
        (FrameType.LIGHT, ImageSubtype.AURORA, False),
        (FrameType.LIGHT, ImageSubtype.INTEGRATION_DEPRECATED, False),
        (FrameType.DARK, ImageSubtype.SUB_FRAME, False),
        (FrameType.FLAT, ImageSubtype.SUB_FRAME, False),
    ])
    def test_lights_and_masters_only(self, frame_type, subtype, expected):
        assert is_eligible(_image(frame_type=frame_type, subtype=subtype)) is expected


class TestNeedsMeasurement:
    def test_never_measured(self):
        assert needs_measurement(_image()) is True

    def test_up_to_date_is_left_alone(self):
        assert needs_measurement(_measured()) is False

    def test_pending_is_not_requeued(self):
        assert needs_measurement(_image(star_metrics_status="PENDING")) is False

    def test_older_algorithm_version(self):
        assert needs_measurement(_measured(star_metrics_version=ALGO_VERSION - 1)) is True

    def test_file_changed_on_disk(self):
        img = _measured(file_last_modified=MTIME + timedelta(hours=1))
        assert needs_measurement(img) is True

    @pytest.mark.parametrize("attempts,expected", [(1, True), (2, True), (3, False)])
    def test_failed_retried_up_to_max_attempts(self, attempts, expected):
        img = _measured(star_metrics_status="FAILED",
                        star_metrics={"file_mtime": MTIME.isoformat(), "attempts": attempts})
        assert needs_measurement(img) is expected

    def test_calibration_frames_never(self):
        assert needs_measurement(_image(frame_type=FrameType.DARK)) is False

    def test_disabled_setting(self):
        with patch.object(quality, "measuring_enabled", return_value=False):
            assert needs_measurement(_image()) is False


class TestApplyResult:
    NOW = datetime(2026, 9, 27, 8, 0, 0)

    def test_ok_result(self):
        img = _image(star_metrics_status="PENDING")
        result = StarMetrics("OK", hfr_px=1.9, fwhm_px=3.6, eccentricity=0.41, star_count=1320,
                             details={"n_fwhm": 100})
        apply_result(img, result, {"FOCPOS": 14820.0}, self.NOW)
        assert (img.star_metrics_status, img.hfr_px, img.fwhm_px, img.eccentricity, img.star_count) == \
            ("OK", 1.9, 3.6, 0.41, 1320)
        assert img.star_metrics_version == ALGO_VERSION
        assert img.star_metrics_at == self.NOW
        assert img.star_metrics["source"] == "MEASURED"
        assert img.star_metrics["hints"] == {"FOCPOS": 14820.0}
        assert img.star_metrics["file_mtime"] == MTIME.isoformat()
        assert needs_measurement(img) is False

    def test_measured_value_wins_over_hint(self):
        img = _image()
        apply_result(img, StarMetrics("OK", hfr_px=1.9, fwhm_px=3.6, eccentricity=0.4, star_count=900),
                     {"HFR": 2.31}, self.NOW)
        assert img.hfr_px == 1.9
        assert img.star_metrics["hints"]["HFR"] == 2.31

    def test_skipped_file_uses_capture_software_hint(self):
        img = _image()
        apply_result(img, StarMetrics("SKIPPED", details={"reason": "NONLINEAR_FORMAT"}),
                     {"HFR": 2.31, "STARS": 845}, self.NOW)
        assert img.star_metrics_status == "HINT"
        assert img.hfr_px == 2.31 and img.star_count == 845 and img.fwhm_px is None
        assert img.star_metrics["source"] == "HINT"

    def test_skipped_without_hint_stays_skipped(self):
        img = _image()
        apply_result(img, StarMetrics("SKIPPED", details={"reason": "NONLINEAR_FORMAT"}), {}, self.NOW)
        assert img.star_metrics_status == "SKIPPED" and img.hfr_px is None

    def test_failures_count_attempts(self):
        img = _image(star_metrics={"attempts": 1})
        apply_result(img, StarMetrics("FAILED", details={"error": "boom"}), {}, self.NOW)
        assert img.star_metrics_status == "FAILED"
        assert img.star_metrics["attempts"] == 2
        assert img.star_metrics["error"] == "boom"

    def test_remeasure_clears_stale_values(self):
        img = _measured(hfr_px=1.9, fwhm_px=3.6, eccentricity=0.4, star_count=900)
        apply_result(img, StarMetrics("NO_STARS", star_count=3), {}, self.NOW)
        assert img.hfr_px is None and img.fwhm_px is None and img.star_count == 3


class TestSweep:
    def test_clause_compiles_for_postgres(self):
        sql = str(sweep_clause(datetime(2026, 9, 27)).compile(dialect=postgresql.dialect()))
        assert "star_metrics_status" in sql and "star_metrics_version" in sql

    def test_skips_while_queue_is_busy(self):
        r = MagicMock()
        r.llen.return_value = 500
        with patch.object(quality, "SessionLocal") as session_local:
            out = quality._sweep_impl(r)
        assert out["status"] == "queue_busy"
        session_local.assert_not_called()

    def test_disabled_backfill(self):
        with patch.object(quality, "backfill_enabled", return_value=False):
            assert quality._sweep_impl(MagicMock())["status"] == "disabled"

    def test_single_sweep_at_a_time(self):
        r = MagicMock()
        r.llen.return_value = 0
        r.set.return_value = False
        assert quality._sweep_impl(r)["status"] == "locked"

    def test_queues_batch_and_marks_pending(self):
        r = MagicMock()
        r.llen.return_value = 0
        r.set.return_value = True
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [11, 12, 13]
        with patch.object(quality, "SessionLocal") as session_local, \
                patch.object(quality.measure_star_metrics, "delay") as delay:
            session_local.return_value.__enter__.return_value = session
            out = quality._sweep_impl(r)
        assert out == {"status": "queued", "queued": 3, "queue_depth": 0}
        assert [c.args[0] for c in delay.call_args_list] == [11, 12, 13]
        session.commit.assert_called_once()
        r.delete.assert_called_once_with(quality.SWEEP_LOCK_KEY)


class TestQueueIfNeeded:
    def test_marks_pending_and_queues(self):
        session, img = MagicMock(), _image()
        with patch.object(quality.measure_star_metrics, "delay") as delay:
            assert quality.queue_if_needed(session, img) is True
        assert img.star_metrics_status == "PENDING"
        delay.assert_called_once_with(1)

    def test_never_raises(self):
        session, img = MagicMock(), _image()
        with patch.object(quality.measure_star_metrics, "delay", side_effect=ConnectionError("redis down")):
            assert quality.queue_if_needed(session, img) is False
        session.rollback.assert_called_once()


class TestHints:
    @pytest.mark.parametrize("name,expected", [
        ("2026-09-26_M31_Ha_300s_HFR2.31_0001.fits", 2.31),
        ("M31_HFR_2.31_STARS_845.fits", 2.31),
        ("M31-HFR-2,31.fits", 2.31),
        ("M31_2.31HFR_0001.fits", 2.31),
        ("M31_Ha_300s_0001.fits", None),
        ("SHFRAME_2.31.fits", None),   # "HFR" must be a token, not a substring
    ])
    def test_hfr_from_filename(self, name, expected):
        assert extract_hints(None, name).get("HFR") == expected

    def test_nina_filename_pattern(self):
        name = ("Cepheus_Cas_LIGHT_B_2023-11-28_18-18-23_60.00s__1x1_0_30_-19.80C_0000_Guide-3.13"
                "_FTemp-C_Focus-_HFR-1.85_Rot-359.80_FWHM-60.77.fits")
        assert extract_hints(None, name) == {"HFR": 1.85, "FWHM": 60.77, "GUIDE_RMS": 3.13}

    @pytest.mark.parametrize("name,pos,temp", [
        ("NGC2392_LIGHT_Ha_0004_Guide-0.65_FTemp--2.37C_Focus-122312_HFR-3.57_Rot-115.25.fits", 122312.0, -2.37),
        ("X_0000_Guide-3.13_FTemp-C_Focus-_HFR-1.85_Rot-359.80.fits", None, None),
        ("X_FTemp-7.5C_Focus-4410_0001.fits", 4410.0, 7.5),
    ])
    def test_nina_focuser_tokens(self, name, pos, temp):
        hints = extract_hints(None, name)
        assert hints.get("FOCPOS") == pos and hints.get("FOCTEMP") == temp

    def test_star_count_from_filename(self):
        assert extract_hints(None, "M31_HFR_2.31_STARS_845.fits")["STARS"] == 845

    def test_header_values_and_precedence(self):
        header = {"HFR": 1.88, "FOCPOS": 14820, "FOCTEMP": "7.5", "PIERSIDE": "West", "AIRMASS": 1.21}
        hints = extract_hints(header, "M31_HFR_2.31.fits")
        assert hints == {"HFR": 1.88, "FOCPOS": 14820.0, "FOCTEMP": 7.5, "PIERSIDE": "WEST", "AIRMASS": 1.21}

    def test_zero_means_not_measured(self):
        assert extract_hints({"HFR": 0, "STARS": 0}, "x_HFR_0.00.fits") == {}
