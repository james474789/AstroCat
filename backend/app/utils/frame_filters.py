"""
Shared "lights only" predicates (F1).

F2, F7 and F16 all need a reliable way to exclude calibration frames from
integration totals and grouping logic. Import these helpers rather than
inlining the expression (see docs/design/README.md §2.1).
"""

from sqlalchemy import and_

from app.models.image import FrameType, Image, ImageSubtype


def light_subs_clause():
    """Frames that count toward integration totals: LIGHT sub-frames only."""
    return and_(Image.frame_type == FrameType.LIGHT, Image.subtype == ImageSubtype.SUB_FRAME)


def lights_clause():
    """Any LIGHT frame, regardless of processing subtype."""
    return Image.frame_type == FrameType.LIGHT
