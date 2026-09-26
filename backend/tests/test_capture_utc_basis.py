"""R1 §3.2: UTC for local-clock rows without a site, with capture_utc_basis."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.equipment_assignment import (
    BASIS_CAMERA_UTC, BASIS_DEFAULT_SITE_TZ, BASIS_SITE_TZ, CLOCK_LOCAL, CLOCK_UTC, SiteInfo,
    apply_equipment, capture_utc_with_basis,
)

JULY = datetime(2026, 7, 10, 23, 30)
DEFAULT = SiteInfo(id=1, latitude=56.0, longitude=0.0, timezone="Europe/London", is_default=True)
OTHER = SiteInfo(id=2, latitude=51.48, longitude=0.0, timezone="Europe/Paris", is_default=False)


def test_camera_utc_clock_wins_without_a_site():
    assert capture_utc_with_basis("EXIF_LOCAL", JULY, None, "Europe/London", CLOCK_UTC) == (JULY, BASIS_CAMERA_UTC)


def test_default_site_zone_for_siteless_rows():
    utc, basis = capture_utc_with_basis("EXIF_LOCAL", JULY, None, "Europe/London", CLOCK_LOCAL)
    assert (utc, basis) == (datetime(2026, 7, 10, 22, 30), BASIS_DEFAULT_SITE_TZ)
    utc, basis = capture_utc_with_basis("FITS_LOCAL", JULY, None, "Europe/London")
    assert basis == BASIS_DEFAULT_SITE_TZ


def test_own_site_zone_is_site_tz():
    utc, basis = capture_utc_with_basis("EXIF_LOCAL", JULY, "Europe/Paris", "Europe/London")
    assert (utc, basis) == (datetime(2026, 7, 10, 21, 30), BASIS_SITE_TZ)


def test_no_default_site_leaves_row_untouched():
    assert capture_utc_with_basis("EXIF_LOCAL", JULY, None, None) == (None, None)


def test_non_local_sources_and_offsets_are_never_touched():
    assert capture_utc_with_basis("FITS_UTC", JULY, None, "Europe/London") == (None, None)
    assert capture_utc_with_basis("EXIF_LOCAL", JULY, None, "Europe/London", offset_value="+02:00") == (None, None)
    assert capture_utc_with_basis("EXIF_LOCAL", None, None, "Europe/London") == (None, None)


def _siteless_image(**kw):
    base = dict(rig_id=None, rig_source=None, camera_name="Canon EOS 600D", width_pixels=None, height_pixels=None,
                binning=None, pixel_scale_arcsec=None, focal_length=None,
                raw_header={"EXIF:EXIF DateTimeOriginal": "2026:07:10 23:30:00"},
                site_latitude=None, site_longitude=None, site_id=None, capture_time_source="EXIF_LOCAL",
                capture_date=JULY, capture_date_utc=None, capture_utc_basis=None)
    return SimpleNamespace(**{**base, **kw})


def test_indexer_hook_uses_default_site_for_siteless_rows():
    image = _siteless_image()
    apply_equipment(image, [], [DEFAULT, OTHER])
    assert image.capture_date_utc == datetime(2026, 7, 10, 22, 30)
    assert image.capture_utc_basis == BASIS_DEFAULT_SITE_TZ

    image = _siteless_image()
    apply_equipment(image, [], [DEFAULT], {"canon eos 600d": CLOCK_UTC})
    assert (image.capture_date_utc, image.capture_utc_basis) == (JULY, BASIS_CAMERA_UTC)

    image = _siteless_image()
    apply_equipment(image, [], [OTHER])  # no default site
    assert (image.capture_date_utc, image.capture_utc_basis) == (None, None)


# --- batch task: _fill_capture_utc against a fake session --------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Serves images rows to the batched SELECT and applies UPDATE params in memory."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.updates = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.strip().upper().startswith("UPDATE"):
            for p in params:
                self.rows[p["id"]]["capture_date_utc"] = p["utc"]
                self.rows[p["id"]]["capture_utc_basis"] = p["basis"]
                self.updates += 1
            return _Result([])
        after, limit = params["after"], params["limit"]
        out = [dict(r) for i, r in sorted(self.rows.items()) if i > after][:limit]
        return _Result(out)

    def commit(self):
        pass


def _row(i, **kw):
    base = dict(id=i, camera_name="Canon EOS 600D", capture_date=JULY, capture_date_utc=None, capture_utc_basis=None,
                capture_time_source="EXIF_LOCAL", site_id=None, date_loc=None,
                exif_original="2026:07:10 23:30:00", offset_value=None)
    return {**base, **kw}


def test_fill_capture_utc_bases_and_idempotence():
    from app.tasks.equipment import _fill_capture_utc

    session = FakeSession([
        _row(1),                                            # site-less -> default zone
        _row(2, site_id=2),                                 # own site -> SITE_TZ
        _row(3, camera_name="Canon EOS R7"),                # UTC-clock camera
        _row(4, capture_time_source="FITS_UTC", capture_date_utc=JULY),  # never touched
    ])
    # FITS_UTC rows aren't selected by the real SQL; mimic that filter.
    session.rows.pop(4)
    summary = {"utc_filled": 0}
    _fill_capture_utc(session, [DEFAULT, OTHER], {"canon eos r7": CLOCK_UTC}, "all", summary)
    assert session.rows[1]["capture_utc_basis"] == BASIS_DEFAULT_SITE_TZ
    assert session.rows[1]["capture_date_utc"] == datetime(2026, 7, 10, 22, 30)
    assert session.rows[2]["capture_utc_basis"] == BASIS_SITE_TZ
    assert session.rows[3]["capture_utc_basis"] == BASIS_CAMERA_UTC and session.rows[3]["capture_date_utc"] == JULY
    assert summary["utc_filled"] == 3
    assert summary["utc_by_basis"] == {BASIS_DEFAULT_SITE_TZ: 1, BASIS_SITE_TZ: 1, BASIS_CAMERA_UTC: 1}

    # Second pass: nothing changes.
    session.updates = 0
    summary2 = {"utc_filled": 0}
    _fill_capture_utc(session, [DEFAULT, OTHER], {"canon eos r7": CLOCK_UTC}, "all", summary2)
    assert session.updates == 0 and summary2["utc_filled"] == 0
    assert summary2["utc_by_basis"] == summary["utc_by_basis"]


def test_fill_capture_utc_without_default_site_leaves_siteless_rows_null():
    from app.tasks.equipment import _fill_capture_utc

    session = FakeSession([_row(1)])
    summary = {"utc_filled": 0}
    _fill_capture_utc(session, [OTHER], {}, "all", summary)
    assert session.rows[1]["capture_date_utc"] is None and session.rows[1]["capture_utc_basis"] is None
    assert session.updates == 0


# --- data migration 0008 ------------------------------------------------------

class _FakeSessionCtx:
    def __init__(self, has_default):
        self.has_default = has_default

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt):
        return SimpleNamespace(first=lambda: (1,) if self.has_default else None)


def test_0008_skips_without_default_site():
    from app.services.data_migrations import get_spec

    with patch("app.database.SessionLocal", lambda: _FakeSessionCtx(False)), \
         patch("app.tasks.equipment.run_assignment") as run:
        assert get_spec("0008_fill_utc_default_site").run() == {"skipped": "no default site"}
    run.assert_not_called()


def test_0008_runs_full_assignment_and_is_repeatable():
    import json
    from app.services.data_migrations import REGISTRY, get_spec

    assert REGISTRY[-1].id == "0008_fill_utc_default_site"
    calls = []
    fake = {"status": "completed", "utc_filled": 5, "utc_by_basis": {"DEFAULT_SITE_TZ": 5},
            "rig_changed": 0, "site_changed": 0, "clock_modes": {}}
    with patch("app.database.SessionLocal", lambda: _FakeSessionCtx(True)), \
         patch("app.tasks.equipment.run_assignment", side_effect=lambda scope: calls.append(scope) or fake):
        first = get_spec("0008_fill_utc_default_site").run()
        second = get_spec("0008_fill_utc_default_site").run()
    assert calls == ["all", "all"]
    assert first == second and first["utc_by_basis"] == {"DEFAULT_SITE_TZ": 5}
    json.dumps(first)
