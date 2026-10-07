from app.services import thumbnail_stats as ts
from app.services.thumbnails import ThumbnailGenerator


def test_delta_new_file():
    assert ts.delta_for_write(None, 100) == (1, 100)


def test_delta_overwrite_changes_size_only():
    assert ts.delta_for_write(100, 70) == (0, -30)


def test_delta_untouched_or_failed():
    assert ts.delta_for_write(100, 100) == (0, 0)
    assert ts.delta_for_write(None, None) == (0, 0)


def test_file_size(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 42)
    assert ts.file_size(str(f)) == 42
    assert ts.file_size(str(tmp_path / "missing.jpg")) is None


def test_thumb_path_for_is_deterministic_and_matches_generate_naming(tmp_path):
    p = ThumbnailGenerator.thumb_path_for("/data/m1/img001.fits", str(tmp_path))
    assert p == ThumbnailGenerator.thumb_path_for("/data/m1/img001.fits", str(tmp_path))
    assert p != ThumbnailGenerator.thumb_path_for("/data/m2/img001.fits", str(tmp_path))
    assert p.endswith("_thumb.jpg") and "img001_" in p


def test_record_write_skips_db_when_nothing_changed(tmp_path, monkeypatch):
    f = tmp_path / "t.jpg"
    f.write_bytes(b"x" * 10)
    called = []
    monkeypatch.setattr(ts, "apply_delta", lambda *a, **k: called.append(a))
    ts.record_write(str(f), 10)  # same size as before: no write, no DB session
    assert called == []


def test_record_write_never_raises(tmp_path, monkeypatch):
    f = tmp_path / "t.jpg"
    f.write_bytes(b"x" * 10)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ts, "apply_delta", boom)
    ts.record_write(str(f), None)  # must swallow
