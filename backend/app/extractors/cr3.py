"""
Minimal Canon CR3 EXIF reader.

CR3 is an ISO base media file, not a TIFF, so exifread does not recognise it. Canon stores the
EXIF in a `uuid` box inside `moov`: CMT1 is IFD0 (make, model), CMT2 the Exif IFD (exposure,
ISO, capture time, lens). Each is a small standalone TIFF stream. Pure Python, stdlib only.
"""

import struct
from datetime import datetime
from typing import Any, Dict, Optional

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
_MAX_MOOV = 64 * 1024 * 1024


def _boxes(buf: bytes, start: int = 0, end: Optional[int] = None):
    """Yield (type, payload_start, payload_end) for each box in buf[start:end]."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, box_type = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield box_type, pos + header, pos + size
        pos += size


def _read_moov(path: str) -> Optional[bytes]:
    with open(path, "rb") as f:
        while True:
            head = f.read(8)
            if len(head) < 8:
                return None
            size, box_type = struct.unpack(">I4s", head)
            header = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                header = 16
            if size < header:
                return None  # open-ended or corrupt box before moov
            if box_type == b"moov":
                if size - header > _MAX_MOOV:
                    return None
                return f.read(size - header)
            f.seek(size - header, 1)


def _parse_ifd(tiff: bytes, offset: Optional[int] = None) -> Dict[int, Any]:
    """Tag -> value for one IFD of a TIFF stream (first IFD unless offset is given)."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return {}
    e = "<" if tiff[:2] == b"II" else ">"
    if offset is None:
        offset = struct.unpack_from(e + "I", tiff, 4)[0]
    out: Dict[int, Any] = {}
    if offset + 2 > len(tiff):
        return out
    count = struct.unpack_from(e + "H", tiff, offset)[0]
    for i in range(count):
        pos = offset + 2 + i * 12
        if pos + 12 > len(tiff):
            break
        tag, typ, n = struct.unpack_from(e + "HHI", tiff, pos)
        size = _TYPE_SIZES.get(typ)
        if not size or n == 0:
            continue
        total = size * n
        data_pos = pos + 8 if total <= 4 else struct.unpack_from(e + "I", tiff, pos + 8)[0]
        if data_pos + total > len(tiff):
            continue
        if typ == 2:
            out[tag] = tiff[data_pos:data_pos + total].split(b"\0")[0].decode("latin-1").strip()
        elif typ == 3:
            out[tag] = struct.unpack_from(e + "H", tiff, data_pos)[0]
        elif typ == 4:
            out[tag] = struct.unpack_from(e + "I", tiff, data_pos)[0]
        elif typ == 5:
            num, den = struct.unpack_from(e + "II", tiff, data_pos)
            if den:
                out[tag] = num / den
    return out


def read_cr3_exif(path: str) -> Dict[str, Any]:
    """Return camera_name, exposure_time_seconds, iso_speed, capture_date, lens_model, aperture,
    focal_length for a CR3 file; keys the file doesn't carry are left out. Never raises."""
    result: Dict[str, Any] = {}
    try:
        moov = _read_moov(str(path))
        if not moov:
            return result
        cmt: Dict[bytes, bytes] = {}
        for box_type, start, end in _boxes(moov):
            if box_type == b"uuid" and moov[start:start + 16] == CANON_UUID:
                for sub_type, s, e_ in _boxes(moov, start + 16, end):
                    if sub_type in (b"CMT1", b"CMT2"):
                        cmt[sub_type] = moov[s:e_]
        ifd0 = _parse_ifd(cmt.get(b"CMT1", b""))
        exif = _parse_ifd(cmt.get(b"CMT2", b""))

        make, model = ifd0.get(0x010F, ""), ifd0.get(0x0110, "")
        if model:
            result["camera_name"] = model if make and model.lower().startswith(make.lower()) else f"{make} {model}".strip()
        if 0x829A in exif:
            result["exposure_time_seconds"] = float(exif[0x829A])
        if 0x8827 in exif:
            result["iso_speed"] = int(exif[0x8827])
        if 0x829D in exif:
            result["aperture"] = float(exif[0x829D])
        if 0x920A in exif:
            result["focal_length"] = float(exif[0x920A])
        if exif.get(0xA434):
            result["lens_model"] = exif[0xA434]
        stamp = exif.get(0x9003) or ifd0.get(0x0132)
        if stamp:
            try:
                result["capture_date"] = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
    except Exception:
        return result
    return result
