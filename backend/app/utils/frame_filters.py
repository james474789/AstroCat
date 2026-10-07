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


PLATE_SOLVABLE_SUBTYPES = (ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER)

# Raw-SQL twin of plate_solvable_clause() for hand-written queries.
PLATE_SOLVABLE_SQL = (
    "frame_type = 'LIGHT' AND subtype IN ('SUB_FRAME', 'INTEGRATION_MASTER')"
)


def plate_solvable_clause():
    """
    Images that can be plate-solved, and so belong in plate-solve stats:
    LIGHT sub-frames and integration masters. Excludes PLANETARY,
    INTEGRATION_DEPRECATED and calibration frames (dark/flat/bias/dark-flat).
    """
    return and_(
        Image.frame_type == FrameType.LIGHT,
        Image.subtype.in_(PLATE_SOLVABLE_SUBTYPES),
    )


def lights_clause():
    """Any LIGHT frame, regardless of processing subtype."""
    return Image.frame_type == FrameType.LIGHT
