import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "caldwell_catalog.json"


def test_catalog_has_all_109_caldwell_objects():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    names = {t["name"] for t in d["targets"]}
    assert names == {f"C{i}" for i in range(1, 110)}
    assert d["target_count"] == 109
