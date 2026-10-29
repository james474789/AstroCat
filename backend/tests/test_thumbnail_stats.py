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


def test_remove_tracked_deletes_files_and_subtracts_them(tmp_path, monkeypatch):
    a = tmp_path / "a_thumb.jpg"
    a.write_bytes(b"x" * 10)
    b = tmp_path / "b_thumb.jpg"
    b.write_bytes(b"x" * 5)
    deltas = []

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def commit(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _S())
    monkeypatch.setattr(ts, "apply_delta", lambda s, c, b_: deltas.append((c, b_)))
    assert ts.remove_tracked([str(a), str(b), str(tmp_path / "gone.jpg")]) == 2
    assert not a.exists() and not b.exists()
    assert deltas == [(-2, -15)]


def test_update_thumbnail_stats_counts_the_directory(tmp_path, monkeypatch):
    from app.config import settings
    from app.tasks import indexer

    (tmp_path / "a_thumb.jpg").write_bytes(b"x" * 10)
    (tmp_path / "b_thumb.jpg").write_bytes(b"x" * 20)
    saved = {}

    class _Stats:
        pass

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def execute(self, stmt):
            class _R:
                def scalar_one_or_none(self_):
                    return saved.setdefault("row", _Stats())
            return _R()

        def add(self, o):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(type(settings), "thumbnail_cache_path", property(lambda self: str(tmp_path)), raising=False)
    monkeypatch.setattr(indexer, "SessionLocal", lambda: _S())
    result = indexer.update_thumbnail_stats()
    assert result["count"] == 2 and result["size_bytes"] == 30
    assert saved["row"].count == 2


def test_regenerate_queues_a_recount(monkeypatch):
    from app.tasks import indexer, thumbnails

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def execute(self, stmt):
            class _R:
                def all(self_):
                    return [(1,), (2,)]
            return _R()

    queued = []
    monkeypatch.setattr(indexer, "SessionLocal", lambda: _S())
    monkeypatch.setattr(thumbnails.generate_thumbnail, "delay", lambda *a, **k: queued.append(("gen", a)))
    monkeypatch.setattr(indexer.update_thumbnail_stats, "apply_async", lambda *a, **k: queued.append(("recount", k)))
    result = indexer.regenerate_thumbnails.run()
    assert result["images_queued"] == 2
    assert [q[0] for q in queued] == ["gen", "gen", "recount", "recount"]
