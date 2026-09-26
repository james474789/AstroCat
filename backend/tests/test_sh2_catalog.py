"""
Tests for the Sharpless (Sh2) catalog data file.

Validates backend/data/sharpless_catalog.json against the shape
app.data.seed.seed_sh2_catalog() expects, and against the model
constraints in app.models.catalog.Sh2Catalog (unique designation,
unique sh2_number, required ra/dec).
"""

import json
import re
from pathlib import Path

import pytest

CATALOG_PATH = Path(__file__).parent.parent / "data" / "sharpless_catalog.json"

DESIGNATION_RE = re.compile(r"^Sh2-(\d+)$")


@pytest.fixture(scope="module")
def sh2_data():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_catalog_file_exists():
    assert CATALOG_PATH.exists()


def test_schema_and_target_count(sh2_data):
    assert sh2_data["schema"] == "space-cats-v1"
    assert sh2_data["target_count"] == len(sh2_data["targets"])
    assert len(sh2_data["targets"]) == 313


def test_designations_are_unique_and_well_formed(sh2_data):
    designations = [t["name"] for t in sh2_data["targets"]]
    assert len(designations) == len(set(designations))
    for name in designations:
        assert DESIGNATION_RE.match(name), f"Unexpected designation format: {name}"


def test_sh2_numbers_are_unique(sh2_data):
    numbers = [int(DESIGNATION_RE.match(t["name"]).group(1)) for t in sh2_data["targets"]]
    assert len(numbers) == len(set(numbers))


def test_all_targets_have_valid_coordinates(sh2_data):
    for t in sh2_data["targets"]:
        assert t["ra"] is not None and 0.0 <= t["ra"] <= 360.0
        assert t["dec"] is not None and -90.0 <= t["dec"] <= 90.0


def test_all_targets_tagged_sharpless(sh2_data):
    assert all(t["catalog"] == "sharpless" for t in sh2_data["targets"])
