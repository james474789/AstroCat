"""
Equipment & Sites models (R0, docs/design/P0-R0-equipment-sites.md §4.1).

A rig is camera + optic (+ optional reducer/Barlow modifier, binning and a
filter set). Images point at a rig (images.rig_id) and a site
(images.site_id); both are assigned automatically with a manual override
(see app/services/equipment_assignment.py).

Sensor modes: an unlocked / different-resolution mode of the same physical
camera (e.g. ASI294MM at 8288x5644, 2.315 um) is a separate Camera row.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# Row provenance for cameras/optics/filters.
SOURCE_DETECTED = "DETECTED"
SOURCE_TELESCOPIUS = "TELESCOPIUS"
SOURCE_MANUAL = "MANUAL"
SOURCE_SEED = "SEED"

OPTIC_KINDS = ("TELESCOPE", "LENS")
FILTER_BANDS = ("L", "R", "G", "B", "Ha", "OIII", "SII", "Hb", "Duo", "None", "Other")
HORIZON_SOURCES = ("LEARNED", "IMPORTED", "MANUAL")

# images.rig_source
RIG_SOURCE_AUTO = "AUTO"
RIG_SOURCE_MANUAL = "MANUAL"

# Rigs that can be mounted at once (several mounts imaging concurrently).
MAX_MOUNTED_RIGS = 5


rig_filters = Table(
    "rig_filters",
    Base.metadata,
    Column("rig_id", Integer, ForeignKey("rigs.id", ondelete="CASCADE"), primary_key=True),
    Column("filter_id", Integer, ForeignKey("filters.id", ondelete="CASCADE"), primary_key=True),
)


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    maker = Column(String(50), nullable=True)
    sensor_width_px = Column(Integer, nullable=True)
    sensor_height_px = Column(Integer, nullable=True)
    pixel_size_um = Column(Float, nullable=True)          # unbinned
    is_color = Column(Boolean, nullable=True)             # OSC/DSLR True, mono False, unknown NULL
    is_cooled = Column(Boolean, nullable=True)
    match_patterns = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    source = Column(String(20), nullable=False, default=SOURCE_MANUAL)
    external_ref = Column(String(50), nullable=True)      # e.g. "telescopius:50600"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Camera(id={self.id}, name='{self.name}')>"


class Optic(Base):
    __tablename__ = "optics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    kind = Column(String(10), nullable=False, default="TELESCOPE")   # TELESCOPE | LENS
    aperture_mm = Column(Float, nullable=True)
    focal_length_mm = Column(Float, nullable=False)
    source = Column(String(20), nullable=False, default=SOURCE_MANUAL)
    external_ref = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Optic(id={self.id}, name='{self.name}', fl={self.focal_length_mm})>"


class Filter(Base):
    __tablename__ = "filters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    band = Column(String(20), nullable=False, default="Other")   # normalize_filter bucket
    bandwidth_nm = Column(Float, nullable=True)
    match_patterns = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    source = Column(String(20), nullable=False, default=SOURCE_MANUAL)
    external_ref = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Filter(id={self.id}, name='{self.name}', band='{self.band}')>"


class Rig(Base):
    __tablename__ = "rigs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), unique=True, nullable=False)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    optic_id = Column(Integer, ForeignKey("optics.id"), nullable=False, index=True)
    modifier_name = Column(String(50), nullable=True)
    modifier_factor = Column(Float, nullable=False, default=1.0, server_default=text("1.0"))
    binning = Column(Integer, nullable=False, default=1, server_default=text("1"))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_mounted = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    mount_name = Column(String(100), nullable=True)
    # Cached by the assignment task: median solved scale of assigned light subs.
    measured_scale_arcsec = Column(Float, nullable=True)
    measured_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    camera = relationship("Camera", lazy="joined")
    optic = relationship("Optic", lazy="joined")
    filters = relationship("Filter", secondary=rig_filters, lazy="selectin", order_by="Filter.id")

    # Up to MAX_MOUNTED_RIGS may be mounted at once (enforced by the API).

    def __repr__(self):
        return f"<Rig(id={self.id}, name='{self.name}')>"


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)              # east-positive
    elevation_m = Column(Float, nullable=True)
    timezone = Column(String(64), nullable=False)          # IANA, e.g. "Europe/London"
    bortle = Column(Integer, nullable=True)
    sqm = Column(Float, nullable=True)
    typical_seeing_arcsec = Column(Float, nullable=False, default=2.5, server_default=text("2.5"))
    is_default = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    horizon = Column(JSONB, nullable=True)                 # [[az_deg, alt_deg], ...] sorted by az
    horizon_source = Column(String(10), nullable=True)     # LEARNED | IMPORTED | MANUAL
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("uq_sites_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )

    def __repr__(self):
        return f"<Site(id={self.id}, name='{self.name}')>"
