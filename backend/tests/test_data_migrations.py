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


def test_p0_migrations_appended_in_order():
    ids = [spec.id for spec in REGISTRY]
    assert ids[:7] == [
        "0001_backfill_frame_types",
        "0002_repair_field_radius",
        "0003_backfill_targets",
        "0004_canonicalize_target_keys",
        "0005_capture_time_provenance",
        "0006_image_site_coordinates",
        "0007_split_barnards_loop",
    ]


def test_0004_recanonicalizes_then_runs_sentinel_pass():
    import json
    from unittest.mock import patch

    calls = []
    with patch("app.scripts.recanonicalize_targets.recanonicalize_targets",
               side_effect=lambda: calls.append("recanon") or {"remapped_keys": 1, "sh2_cross_ids": []}), \
         patch("app.scripts.backfill_targets.backfill_targets",
               side_effect=lambda process_all: calls.append(("backfill", process_all)) or {"processed": 2, "changed": 2}), \
         patch("app.scripts.recanonicalize_targets.mark_unresolved_lights_none",
               side_effect=lambda: calls.append("mark_none") or 0):
        summary = get_spec("0004_canonicalize_target_keys").run()

    assert calls == ["recanon", ("backfill", False), "mark_none"]
    assert summary["sentinel_backfill"] == {"processed": 2, "changed": 2}
    assert summary["marked_none"] == 0
    json.dumps(summary)


def test_0005_and_0006_call_incremental_backfills():
    from unittest.mock import patch

    with patch("app.scripts.backfill_capture_time.backfill_capture_time", return_value={"processed": 0}) as bct, \
         patch("app.scripts.backfill_sites.backfill_image_sites", return_value={"processed": 0}) as bs:
        get_spec("0005_capture_time_provenance").run()
        get_spec("0006_image_site_coordinates").run()
    bct.assert_called_once_with(process_all=False)
    bs.assert_called_once_with(process_all=False)


def test_0007_reresolves_ngc1981():
    from unittest.mock import patch

    with patch("app.scripts.recanonicalize_targets.reresolve_target_keys", return_value={"changed": 3}) as rr:
        assert get_spec("0007_split_barnards_loop").run() == {"changed": 3}
    rr.assert_called_once_with(["NGC1981"])
