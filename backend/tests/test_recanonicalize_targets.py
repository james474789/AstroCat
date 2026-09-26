"""
Tests for app.scripts.recanonicalize_targets (P0 §3.1): the pure remap and
goal-merge helpers, plus the script against an in-memory fake session (in
the mocked-session style of the frame-type backfill tests) - no DB.
"""

from unittest.mock import MagicMock, patch

from app.scripts import recanonicalize_targets as rc
from app.services.targets import AliasIndex


def _index():
    index = AliasIndex()
    index.add_alias("M81", "M81")
    index.add_alias("NGC3031", "M81")
    index.add_alias("Bodes Galaxy", "M81")
    index.add_alias("NGC7635", "NGC7635")
    index.add_alias("C11", "NGC7635")
    index.add_alias("IC1396", "IC1396")
    index.add_alias("Sh2-131", "IC1396")
    index.sh2_cross_ids = [{"sh2": "Sh2-131", "ngc": "IC1396", "separation_deg": 0.1, "via": "rule"}]
    return index


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_compute_key_remap():
    remap = rc.compute_key_remap(
        ["NGC3031", "M81", "C11", "SH2131", "OBJ:BODESGALAXY", "OBJ:SOMETHINGELSE", "NGC9999", None],
        _index(),
    )
    assert remap == {
        "NGC3031": ("M81", False),
        "C11": ("NGC7635", False),
        "SH2131": ("IC1396", False),
        "OBJ:BODESGALAXY": ("M81", True),
    }


def test_compute_key_remap_follows_chain_to_fixed_point():
    index = AliasIndex()
    index.add_alias("A1", "B1")
    index.add_alias("B1", "C1")
    index.add_alias("C1", "C1")
    assert rc.compute_key_remap(["A1", "B1", "C1"], index) == {"A1": ("C1", False), "B1": ("C1", False)}


def test_merge_goal_rows_keeps_larger():
    plan = rc.merge_goal_rows({"Ha": 36000.0, "OIII": 3600.0, "ANY": 100.0}, {"Ha": 7200.0, "OIII": 7200.0})
    assert plan == {
        "Ha": ("raise", 36000.0),
        "OIII": ("drop", 7200.0),
        "ANY": ("move", 100.0),
    }


# ---------------------------------------------------------------------------
# Script against a fake session
# ---------------------------------------------------------------------------

class FakeDB:
    """Tiny in-memory stand-in for the images/target_goals tables."""

    def __init__(self, image_keys, goals):
        # image_keys: {(key, source): count}; goals: {(key, group): seconds}
        self.image_keys = dict(image_keys)
        self.goals = dict(goals)
        self.image_updates = []
        self.commits = 0

    def session(self):
        db = self
        session = MagicMock()

        def execute(stmt, params=None):
            sql = str(stmt)
            params = params or {}
            result = MagicMock()
            if sql.startswith("SELECT DISTINCT target_key FROM images"):
                keys = sorted({k for (k, s) in db.image_keys if k and s != "MANUAL"})
                result.scalars.return_value.all.return_value = keys
            elif sql.startswith("UPDATE images SET target_key"):
                db.image_updates.append((params["old"], params["new"]))
                moved = 0
                for (key, source), count in list(db.image_keys.items()):
                    if key == params["old"] and source != "MANUAL":
                        del db.image_keys[(key, source)]
                        new_source = "HEADER" if source == "HEADER_RAW" else source
                        db.image_keys[(params["new"], new_source)] = db.image_keys.get((params["new"], new_source), 0) + count
                        moved += count
                result.rowcount = moved
            elif sql.startswith("SELECT filter_group, goal_seconds FROM target_goals"):
                result.all.return_value = [(g, v) for (k, g), v in db.goals.items() if k == params["key"]]
            elif sql.startswith("UPDATE target_goals SET target_key"):
                db.goals[(params["new"], params["group"])] = db.goals.pop((params["old"], params["group"]))
            elif sql.startswith("UPDATE target_goals SET goal_seconds"):
                db.goals[(params["new"], params["group"])] = params["value"]
            elif sql.startswith("DELETE FROM target_goals"):
                db.goals.pop((params["old"], params["group"]), None)
            elif sql.startswith("UPDATE images SET target_source"):
                n = db.image_keys.pop((None, None), 0)
                if n:
                    db.image_keys[(None, params["none"])] = db.image_keys.get((None, params["none"]), 0) + n
                result.rowcount = n
            else:  # pragma: no cover - guards against an unexpected statement
                raise AssertionError(f"unexpected SQL: {sql}")
            return result

        def commit():
            db.commits += 1

        session.execute.side_effect = execute
        session.commit.side_effect = commit
        return session


def _run(db):
    with patch("app.services.targets._build_alias_index_sync", return_value=_index()), \
         patch.object(rc, "_clear_targets_cache") as clear_cache:
        summary = rc.recanonicalize_targets(session=db.session())
    clear_cache.assert_called_once()
    return summary


def _fixture_db():
    return FakeDB(
        image_keys={
            ("M81", "HEADER"): 872,
            ("NGC3031", "MATCH"): 519,
            ("NGC3031", "MANUAL"): 3,       # user override: never touched
            ("C11", "MATCH"): 245,
            ("NGC7635", "HEADER"): 400,
            ("SH2131", "MATCH"): 64,
            ("OBJ:BODESGALAXY", "HEADER_RAW"): 10,
            ("OBJ:UNKNOWNTHING", "HEADER_RAW"): 7,
        },
        goals={
            ("NGC3031", "Ha"): 36000.0,   # larger than M81's -> kept
            ("M81", "Ha"): 7200.0,
            ("NGC3031", "L"): 1800.0,     # smaller than M81's -> dropped
            ("M81", "L"): 18000.0,
            ("C11", "OIII"): 3600.0,      # no NGC7635 goal -> moved
        },
    )


def test_recanonicalize_issues_one_bulk_update_per_remapped_key():
    db = _fixture_db()
    summary = _run(db)

    assert sorted(db.image_updates) == sorted([
        ("C11", "NGC7635"),
        ("NGC3031", "M81"),
        ("OBJ:BODESGALAXY", "M81"),
        ("SH2131", "IC1396"),
    ])
    assert summary["remapped_keys"] == 4
    assert summary["rows_updated"] == 519 + 245 + 64 + 10
    assert summary["remaps"]["NGC3031"] == "M81"
    assert summary["sh2_cross_ids"][0]["sh2"] == "Sh2-131"

    assert db.image_keys[("M81", "HEADER")] == 872 + 10   # OBJ: row became HEADER
    assert db.image_keys[("M81", "MATCH")] == 519
    assert db.image_keys[("NGC7635", "MATCH")] == 245
    assert db.image_keys[("IC1396", "MATCH")] == 64
    assert db.image_keys[("NGC3031", "MANUAL")] == 3
    assert db.image_keys[("OBJ:UNKNOWNTHING", "HEADER_RAW")] == 7


def test_recanonicalize_goal_merge_keeps_max():
    db = _fixture_db()
    summary = _run(db)

    assert db.goals == {
        ("M81", "Ha"): 36000.0,
        ("M81", "L"): 18000.0,
        ("NGC7635", "OIII"): 3600.0,
    }
    assert summary["goals_merged"] == 2


def test_recanonicalize_second_run_is_noop():
    db = _fixture_db()
    _run(db)
    keys_after_first, goals_after_first = dict(db.image_keys), dict(db.goals)
    db.image_updates.clear()

    summary = _run(db)

    assert summary["remapped_keys"] == 0
    assert summary["rows_updated"] == 0
    assert db.image_updates == []
    assert db.image_keys == keys_after_first
    assert db.goals == goals_after_first


def test_mark_unresolved_lights_none():
    db = FakeDB(image_keys={(None, None): 57058, ("M31", "HEADER"): 5}, goals={})
    session = db.session()
    assert rc.mark_unresolved_lights_none(session=session) == 57058
    assert db.image_keys == {(None, "NONE"): 57058, ("M31", "HEADER"): 5}
    assert rc.mark_unresolved_lights_none(session=session) == 0
