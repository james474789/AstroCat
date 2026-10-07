"""
WCS-only header cards, shared by the FITS extractor and the sky overlay.

Works on an astropy Header or a stored raw_header / wcs_header dict (XISF
keywords, JSONB rows), whose COMMENT/HISTORY lists, string-typed numbers and
unparsable cards WCS() can't take.
"""

import math
import re
from typing import Mapping

from astropy.io import fits

WCS_KEY = re.compile(
    r"^(NAXIS[12]?|WCSAXES|CTYPE[12]|CRVAL[12]|CRPIX[12]|CDELT[12]|CUNIT[12]|CROTA[12]"
    r"|CD[12]_[12]|PC[12]_[12]|LONPOLE|LATPOLE|EQUINOX|EPOCH|RADESYS|RADECSYS"
    r"|(A|B|AP|BP)_ORDER|(A|B|AP|BP)_\d+_\d+)$"
)

_STRING_KEYS = ("CTYPE", "CUNIT", "RADESYS", "RADECSYS")


def _card_value(header: Mapping, key: str):
    """The card's value, or None when missing, blank or unreadable (astropy parses lazily)."""
    try:
        value = header.get(key)
    except Exception:
        return None
    if value is None or isinstance(value, (Exception, fits.card.Undefined)):
        return None
    return value


def wcs_cards(header: Mapping) -> fits.Header:
    """A clean astropy Header holding only the WCS cards, ready for WCS()."""
    clean = fits.Header()
    for key in list(header.keys()):
        if not isinstance(key, str) or not WCS_KEY.match(key):
            continue
        value = _card_value(header, key)
        if isinstance(value, str) and not key.startswith(_STRING_KEYS):
            try:
                num = float(value)
            except ValueError:
                num = None
            if num is not None and math.isfinite(num):
                value = num
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            clean[key] = value
    return clean


def has_sip(header: Mapping) -> bool:
    """True when the header carries a SIP distortion polynomial."""
    return _card_value(header, "A_ORDER") is not None and "SIP" in str(_card_value(header, "CTYPE1") or "").upper()
