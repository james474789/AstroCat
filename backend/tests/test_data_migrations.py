import re

from app.services.data_migrations import REGISTRY, DataMigrationSpec, get_spec, pending_specs


def test_registry_ids_unique_and_numbered_in_order():
    ids = [spec.id for spec in REGISTRY]
    assert len(ids) == len(set(ids))
    numbers = [int(re.match(r"^(\d{4})_[a-z0-9_]+$", i).group(1)) for i in ids]
    assert numbers == sorted(numbers)


def test_registry_orders_field_radius_before_targets():
    # Target resolution needs frame types and field radii to be in place first.
    ids = [spec.id for spec in REGISTRY]
    assert ids.index("0001_backfill_frame_types") < ids.index("0003_backfill_targets")
    assert ids.index("0002_repair_field_radius") < ids.index("0003_backfill_targets")


def test_pending_specs_skips_applied_and_keeps_order():
    registry = [DataMigrationSpec(f"000{n}_x", "", None) for n in (1, 2, 3)]
    pending = pending_specs(registry, ["0002_x"])
    assert [s.id for s in pending] == ["0001_x", "0003_x"]


def test_pending_specs_none_when_all_applied():
    assert pending_specs(REGISTRY, [s.id for s in REGISTRY]) == []


def test_get_spec():
    assert get_spec("0002_repair_field_radius").id == "0002_repair_field_radius"
    assert get_spec("nope") is None
