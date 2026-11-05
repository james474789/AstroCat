"""QA-42: CR3 files carry their EXIF in Canon uuid boxes, which exifread cannot read."""
import struct
from datetime import datetime

from app.extractors.cr3 import CANON_UUID, read_cr3_exif
from app.extractors.exif_extractor import ExifExtractor


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _tiff(entries):
    """Little-endian TIFF with one IFD. entries: (tag, type, str | int | (num, den))."""
    ifd_len = 2 + 12 * len(entries) + 4
    blob = b""
    rows = b""
    for tag, typ, val in entries:
        if typ == 2:
            data = val.encode() + b"\0"
            count = len(data)
        elif typ == 3:
            data, count = struct.pack("<H", val), 1
        else:  # RATIONAL
            data, count = struct.pack("<II", *val), 1
        if len(data) <= 4:
            rows += struct.pack("<HHI", tag, typ, count) + data.ljust(4, b"\0")
        else:
            rows += struct.pack("<HHII", tag, typ, count, 8 + ifd_len + len(blob))
            blob += data
    return b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(entries)) + rows + b"\0\0\0\0" + blob


def make_cr3(path):
    cmt1 = _tiff([(0x010F, 2, "Canon"), (0x0110, 2, "Canon EOS R8"), (0x0132, 2, "2026:11:01 10:00:00")])
    cmt2 = _tiff([(0x829A, 5, (1, 50)), (0x8827, 3, 1600), (0x9003, 2, "2024:10:17 20:39:57")])
    uuid = _box(b"uuid", CANON_UUID + _box(b"CMT1", cmt1) + _box(b"CMT2", cmt2))
    data = _box(b"ftyp", b"crx \0\0\0\1crx isom") + _box(b"moov", uuid) + _box(b"mdat", b"\0" * 64)
    path.write_bytes(data)


def test_reads_camera_exposure_iso_and_date(tmp_path):
    f = tmp_path / "IMG_1.CR3"
    make_cr3(f)
    meta = read_cr3_exif(str(f))
    assert meta["camera_name"] == "Canon EOS R8"
    assert meta["exposure_time_seconds"] == 0.02
    assert meta["iso_speed"] == 1600
    assert meta["capture_date"] == datetime(2024, 10, 17, 20, 39, 57)


def test_extractor_uses_cr3_reader(tmp_path):
    f = tmp_path / "IMG_1.CR3"
    make_cr3(f)
    meta = ExifExtractor(str(f)).extract()
    assert meta["camera_name"] == "Canon EOS R8"
    assert meta["iso_speed"] == 1600
    assert meta["capture_date"] == datetime(2024, 10, 17, 20, 39, 57)


def test_garbage_file_yields_nothing(tmp_path):
    f = tmp_path / "bad.cr3"
    f.write_bytes(b"not a cr3 at all")
    assert read_cr3_exif(str(f)) == {}


def test_backfill_cr3_exif_fills_only_empty_fields(tmp_path):
    """QA-42: CR3 rows indexed before the reader existed get their EXIF; real values stay."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.scripts.backfill_cr3_exif import backfill_cr3_exif

    cr3 = tmp_path / "a.CR3"
    make_cr3(cr3)
    row = SimpleNamespace(
        id=7, file_path=str(cr3), raw_header=None, file_last_modified=datetime(2026, 11, 1, 10, 0, 0),
        camera_name=None, exposure_time_seconds=None, iso_speed=800, capture_date=datetime(2026, 11, 1, 10, 0, 0),
        lens_model=None, aperture=None, focal_length=None, capture_time_source="FILE_MTIME",
    )
    session = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.scalars.return_value.all.return_value = [row]
    second.scalars.return_value.all.return_value = []
    session.execute.side_effect = [first, MagicMock(), second]

    summary = backfill_cr3_exif(session=session)

    assert summary == {"processed": 1, "updated": 1, "unreadable": 0}
    batch = session.execute.call_args_list[1].args[1]
    assert len(batch) == 1
    values = batch[0]
    assert values["camera_name"] == "Canon EOS R8"
    assert values["exposure_time_seconds"] == 1 / 50
    assert values["capture_date"] == datetime(2024, 10, 17, 20, 39, 57)  # replaces mtime placeholder
    assert "iso_speed" not in values  # existing value kept
    assert values["capture_time_source"] != "FILE_MTIME"
