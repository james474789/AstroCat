"""
Tests for update_mount_stats (D2 A1).

Mount counts used to group by the first two path levels, so any root that
wasn't exactly /x/y (a deeper Docker root, a Windows drive path, a macOS home
folder) showed 0. Each configured root is now counted with its own prefix
match, escaped with '!' because Postgres' default LIKE escape is a backslash.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.models.system_stats import SystemStats
from app.tasks.indexer import update_mount_stats
from app.utils.path_security import root_prefix_pattern


@pytest.mark.parametrize("root, exact, pattern", [
    ("/data/mount1", "/data/mount1", "/data/mount1/%"),
    ("/data/images/astro/", "/data/images/astro", "/data/images/astro/%"),
    ("D:\\Astro", "D:\\Astro", "D:\\Astro\\%"),
    ("D:\\Astro\\", "D:\\Astro", "D:\\Astro\\%"),
    ("D:/Astro/", "D:\\Astro", "D:\\Astro\\%"),
    ("/Users/me/My_Photos", "/Users/me/My_Photos", "/Users/me/My!_Photos/%"),
    ("/data/100%_raw!", "/data/100%_raw!", "/data/100!%!_raw!!/%"),
])
def test_root_prefix_pattern(root, exact, pattern):
    assert root_prefix_pattern(root) == (exact, pattern)


def test_update_mount_stats_queries_each_root_with_escape(monkeypatch):
    roots = ["/data/mount1", "D:\\Astro\\", "/Users/me/My_Photos"]
    monkeypatch.setattr(settings, "image_paths", ",".join(roots))

    counts = {
        "/data/mount1": (3, 300),
        "D:\\Astro": (5, 500),
        "/Users/me/My_Photos": (0, 0),
    }
    executed = []
    added = []

    def fake_execute(stmt, params=None):
        result = MagicMock()
        if params is not None:
            executed.append((str(stmt), params))
            result.one.return_value = counts[params["exact"]]
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session = MagicMock()
    session.execute.side_effect = fake_execute
    session.add.side_effect = added.append
    session_cm = MagicMock()
    session_cm.__enter__.return_value = session

    with patch("app.tasks.indexer.SessionLocal", return_value=session_cm):
        update_mount_stats.run()

    assert len(executed) == 3
    for sql, _ in executed:
        assert "ESCAPE '!'" in sql
    assert [p["pattern"] for _, p in executed] == [
        "/data/mount1/%", "D:\\Astro\\%", "/Users/me/My!_Photos/%",
    ]

    by_category = {s.category: s for s in added}
    assert all(isinstance(s, SystemStats) for s in added)
    # The category keeps the root exactly as IMAGE_PATHS spells it.
    assert set(by_category) == {f"mount:{r}" for r in roots}
    assert by_category["mount:/data/mount1"].count == 3
    assert by_category["mount:D:\\Astro\\"].count == 5
    assert by_category["mount:D:\\Astro\\"].size_bytes == 500
    assert by_category["mount:/Users/me/My_Photos"].count == 0
    session.commit.assert_called_once()
