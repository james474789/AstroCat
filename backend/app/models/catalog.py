"""
Catalog Models
Stores Messier and NGC catalog data for matching against images.
"""

from sqlalchemy import Column, Integer, String, Float, Text
from geoalchemy2 import Geography

from app.database import Base


class MessierCatalog(Base):
    """
    Messier Catalog (110 deep-sky objects).
    Used for matching images to well-known objects.
    """
    __tablename__ = "messier_catalog"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Designation (e.g., "M1", "M31", "M42")
    designation = Column(String(10), unique=True, nullable=False, index=True)
    messier_number = Column(Integer, unique=True, nullable=False)
    
    # Common names
    common_name = Column(String(100), nullable=True)  # e.g., "Crab Nebula", "Andromeda Galaxy"
    
    # NGC cross-reference
    ngc_designation = Column(String(20), nullable=True)  # e.g., "NGC 1952"
    
    # Coordinates (J2000)
    ra_degrees = Column(Float, nullable=False)   # Right Ascension (0-360)
    dec_degrees = Column(Float, nullable=False)  # Declination (-90 to +90)
    
    # PostGIS location for spatial queries
    location = Column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=True
    )
    
    # Object Properties
    object_type = Column(String(50), nullable=False)  # e.g., "Nebula", "Galaxy", "Globular Cluster"
    constellation = Column(String(50), nullable=True)
    
    # Visual Properties
    apparent_magnitude = Column(Float, nullable=True)
    angular_size_arcmin = Column(String(30), nullable=True)  # e.g., "70 × 50" for galaxies
    
    # Advanced Properties
    axis_ratio = Column(Float, nullable=True)
    position_angle = Column(Float, nullable=True)
    pgc_designation = Column(String(20), nullable=True)
    
    # Distance
    distance_light_years = Column(Float, nullable=True)
    
    # Description
    description = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<Messier({self.designation}, {self.common_name or self.object_type})>"


class NGCCatalog(Base):
    """
    New General Catalogue (NGC) - ~7,840 deep-sky objects.
    Larger catalog for comprehensive matching.
    """
    __tablename__ = "ngc_catalog"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Designation (e.g., "NGC 224", "NGC 7000")
    designation = Column(String(20), unique=True, nullable=False, index=True)
    ngc_number = Column(Integer, nullable=False, index=True)
    
    # Common name (if any)
    common_name = Column(String(100), nullable=True)
    
    # Messier cross-reference (if applicable)
    messier_designation = Column(String(10), nullable=True)
    
    # IC cross-reference (many NGC objects have IC numbers too)
    ic_designation = Column(String(20), nullable=True)
    
    # Coordinates (J2000)
    ra_degrees = Column(Float, nullable=False)
    dec_degrees = Column(Float, nullable=False)
    
    # PostGIS location for spatial queries
    location = Column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=True
    )
    
    # Object Properties
    object_type = Column(String(50), nullable=True)  # Galaxy, Nebula, Cluster, etc.
    hubble_type = Column(String(20), nullable=True)  # Galaxy morphology (e.g., "Sb", "E0")
    constellation = Column(String(50), nullable=True)
    
    # Visual Properties
    apparent_magnitude = Column(Float, nullable=True)
    b_magnitude = Column(Float, nullable=True)  # Blue magnitude
    surface_brightness = Column(Float, nullable=True)
    
    # Angular size
    major_axis_arcmin = Column(Float, nullable=True)
    minor_axis_arcmin = Column(Float, nullable=True)
    position_angle = Column(Float, nullable=True)  # Degrees
    
    # Redshift/Distance
    redshift = Column(Float, nullable=True)
    
    # Notes
    notes = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<NGC({self.designation}, {self.object_type or 'Unknown'})>"
    
    @property
    def angular_size_display(self) -> str:
        """Format angular size for display."""
        if self.major_axis_arcmin and self.minor_axis_arcmin:
            return f"{self.major_axis_arcmin:.1f}' × {self.minor_axis_arcmin:.1f}'"
        elif self.major_axis_arcmin:
            return f"{self.major_axis_arcmin:.1f}'"
        return "Unknown"


class NamedStarCatalog(Base):
    """
    Named Stars Catalog (~3671 objects).
    Sourced from custom list (NamedStars.csv).
    """
    __tablename__ = "named_star_catalog"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Primary Identifier from CSV
    designation = Column(String(50), unique=True, nullable=False, index=True)
    
    # Common Name (if available)
    common_name = Column(String(100), nullable=True, index=True)
    
    # Cross References
    hip_id = Column(String(20), nullable=True) # "HIP" column
    hd_id = Column(String(20), nullable=True)  # "HD" column
    
    # Coordinates (J2000)
    ra_degrees = Column(Float, nullable=False)   # "alpha" column
    dec_degrees = Column(Float, nullable=False)  # "delta" column
    
    # PostGIS location for spatial queries
    location = Column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=True
    )
    
    # Properties
    magnitude = Column(Float, nullable=True)     # "magnitude" column
    spectral_type = Column(String(20), nullable=True) # "Spectral type" column
    
    def __repr__(self):
        return f"<NamedStar({self.designation}, {self.common_name or 'N/A'})>"
