"""
Catalog Matching Service
Logic for matching images to astronomical catalogs based on WCS coordinates.
"""

from typing import List, Tuple, Optional
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
import math

from app.models.image import Image
from app.models.catalog import MessierCatalog, NGCCatalog, NamedStarCatalog
from app.models.matches import ImageCatalogMatch, CatalogType


class CatalogMatcher:
    """Service to match images against catalogs."""
    
    def __init__(self, session: AsyncSession):
        self.session = session

    async def match_image(self, image_id: int) -> int:
        """
        Find and save matches for a specific image.
        Only saves matches that are visible within the rectangular image bounds.
        Returns number of new matches found.
        """
        # Get image coordinates
        image = await self.session.get(Image, image_id)
        if not image or not image.is_plate_solved:
            return 0
            
        if not image.ra_center_degrees or not image.dec_center_degrees:
            return 0
            
        # Determine search radius
        radius = image.field_radius_degrees or 1.0
        
        # Clear existing automatic matches to avoid duplicates
        # Use ORM delete to ensure session consistency
        from sqlalchemy import delete
        await self.session.execute(
            delete(ImageCatalogMatch).where(
                ImageCatalogMatch.image_id == image_id,
                ImageCatalogMatch.match_source == 'AUTOMATIC'
            )
        )
        await self.session.flush()
        
        count = 0
        all_matches = []
        
        # Match Messier
        messier_rows = await self._find_messier_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.MESSIER, messier_rows))
        
        # Match NGC
        ngc_rows = await self._find_ngc_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.NGC, ngc_rows))

        # Match Named Stars
        star_rows = await self._find_named_stars_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.NAMED_STAR, star_rows))
        
        # Attempt to construct WCS for pixel validation
        wcs = await self._construct_wcs(image)
        
        # Validate matches and save only those within image bounds
        seen_keys = set()
        for cat_type, rows in all_matches:
            for row in rows:
                desig = row.designation
                key = (cat_type, desig)
                if key in seen_keys:
                    continue
                    
                # Skip if WCS validation enabled and object not in bounds
                if wcs is not None:
                    coords = await self._get_catalog_coords(cat_type, desig)
                    if coords is None:
                        continue  # Skip if coordinates not found
                    
                    ra, dec = coords
                    if not self._is_in_image_bounds(wcs, ra, dec, image.width_pixels, image.height_pixels):
                        continue  # Skip objects outside image bounds
                
                seen_keys.add(key)
                
                match = ImageCatalogMatch(
                    image_id=image_id,
                    catalog_type=cat_type,
                    catalog_designation=desig,
                    angular_separation_degrees=row.dist,
                    is_in_field=True,
                    match_source="AUTOMATIC",
                    confidence_score=1.0 - (row.dist / 5.0)
                )
                self.session.add(match)
                count += 1
        
        await self.session.commit()
        return count

    async def _find_messier_in_field(self, ra: float, dec: float, radius: float):
        """Find Messier objects within radius."""
        query = text("""
            SELECT designation, 
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM messier_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
        """)
        
        radius_meters = radius * 111320
        
        result = await self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    async def _find_ngc_in_field(self, ra: float, dec: float, radius: float):
        """Find NGC objects within radius."""
        query = text("""
            SELECT designation,
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM ngc_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
            LIMIT 50
        """)
        
        radius_meters = radius * 111320
        
        result = await self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    async def _find_named_stars_in_field(self, ra: float, dec: float, radius: float):
        """Find Named Stars within radius."""
        query = text("""
            SELECT designation,
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM named_star_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
            LIMIT 50
        """)
        
        radius_meters = radius * 111320
        
        result = await self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    async def _construct_wcs(self, image: Image):
        """
        Construct WCS from image metadata.
        Returns WCS object or None if construction fails.
        """
        try:
            from astropy.wcs import WCS
            
            # Require all essential parameters
            if not all(v is not None for v in [
                image.ra_center_degrees,
                image.dec_center_degrees,
                image.pixel_scale_arcsec,
                image.width_pixels,
                image.height_pixels
            ]):
                return None
            
            wcs = WCS(naxis=2)
            wcs.wcs.crpix = [image.width_pixels / 2.0, image.height_pixels / 2.0]
            wcs.wcs.crval = [image.ra_center_degrees, image.dec_center_degrees]
            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            
            # Convert arcsec/pixel to deg/pixel
            scale = image.pixel_scale_arcsec / 3600.0
            
            # Handle rotation
            rot_raw = image.rotation_degrees or 0.0
            rad = math.radians(rot_raw)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            
            # Get parity from header (defaults to 1 for Normal)
            parity = 1
            if image.raw_header and isinstance(image.raw_header, dict):
                parity = image.raw_header.get('astrometry_parity', 1)
            
            # Scale logic (Web Standard - Top Left Origin)
            s_x = -scale * parity
            s_y = -scale
            
            wcs.wcs.cd = [
                [s_x * cos_a, -s_y * sin_a],
                [s_x * sin_a, s_y * cos_a]
            ]
            
            # Verify WCS is valid
            if not wcs.is_celestial:
                return None
                
            return wcs
            
        except Exception as e:
            # Log error but continue without WCS validation
            print(f"Failed to construct WCS for image {image.id}: {e}")
            return None

    async def _get_catalog_coords(self, cat_type: CatalogType, designation: str) -> Optional[Tuple[float, float]]:
        """
        Fetch RA/Dec coordinates for a catalog object.
        Returns (ra, dec) tuple or None if not found.
        """
        try:
            if cat_type == CatalogType.MESSIER:
                result = await self.session.execute(
                    select(MessierCatalog).where(MessierCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
            elif cat_type == CatalogType.NGC or cat_type == CatalogType.IC:
                result = await self.session.execute(
                    select(NGCCatalog).where(NGCCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
            elif cat_type == CatalogType.NAMED_STAR:
                # Try direct match first
                result = await self.session.execute(
                    select(NamedStarCatalog).where(NamedStarCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                
                # Try normalized match (no spaces)
                from sqlalchemy import func
                result = await self.session.execute(
                    select(NamedStarCatalog).where(
                        func.replace(NamedStarCatalog.designation, ' ', '') == designation.replace(' ', '')
                    )
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
        except Exception as e:
            print(f"Error fetching coordinates for {cat_type} {designation}: {e}")
            
        return None

    def _is_in_image_bounds(self, wcs, ra: float, dec: float, width: int, height: int) -> bool:
        """
        Check if celestial coordinates fall within image bounds.
        Uses same margin as images.py (100 pixels).
        """
        try:
            x, y = wcs.world_to_pixel_values(ra, dec)
            
            margin = 100
            if -margin <= x <= width + margin and -margin <= y <= height + margin:
                return True
                
        except Exception as e:
            # If transformation fails, exclude the object
            print(f"WCS transformation failed for RA={ra}, Dec={dec}: {e}")
            
        return False

    async def _save_matches(self, image_id: int, cat_type: CatalogType, matches: List) -> int:
        """Save match records."""
        count = 0
        for row in matches:
            match = ImageCatalogMatch(
                image_id=image_id,
                catalog_type=cat_type,
                catalog_designation=row.designation,
                angular_separation_degrees=row.dist,
                is_in_field=True,
                match_source="AUTOMATIC",
                confidence_score=1.0 - (row.dist / 5.0)
            )
            self.session.add(match)
            count += 1
        return count


class SyncCatalogMatcher:
    """Service to match images against catalogs using a synchronous session."""
    
    def __init__(self, session):
        self.session = session

    def match_image(self, image_id: int) -> int:
        """
        Find and save matches for a specific image (synchronous).
        Only saves matches that are visible within the rectangular image bounds.
        """
        image = self.session.get(Image, image_id)
        if not image or not image.is_plate_solved:
            return 0
            
        if not image.ra_center_degrees or not image.dec_center_degrees:
            return 0
            
        radius = image.field_radius_degrees or 1.0
        
        from sqlalchemy import delete
        self.session.execute(
            delete(ImageCatalogMatch).where(
                ImageCatalogMatch.image_id == image_id,
                ImageCatalogMatch.match_source == 'AUTOMATIC'
            )
        )
        self.session.flush()
        
        count = 0
        all_matches = []
        
        # Match Messier
        messier_rows = self._find_messier_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.MESSIER, messier_rows))
        
        # Match NGC
        ngc_rows = self._find_ngc_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.NGC, ngc_rows))

        # Match Named Stars
        star_rows = self._find_named_stars_in_field(
            image.ra_center_degrees, image.dec_center_degrees, radius
        )
        all_matches.append((CatalogType.NAMED_STAR, star_rows))
        
        # Attempt to construct WCS for pixel validation
        wcs = self._construct_wcs(image)
        
        # Validate matches and save only those within image bounds
        seen_keys = set()
        for cat_type, rows in all_matches:
            for row in rows:
                desig = row.designation
                key = (cat_type, desig)
                if key in seen_keys:
                    continue
                    
                # Skip if WCS validation enabled and object not in bounds
                if wcs is not None:
                    coords = self._get_catalog_coords(cat_type, desig)
                    if coords is None:
                        continue  # Skip if coordinates not found
                    
                    ra, dec = coords
                    if not self._is_in_image_bounds(wcs, ra, dec, image.width_pixels, image.height_pixels):
                        continue  # Skip objects outside image bounds
                
                seen_keys.add(key)
                
                match = ImageCatalogMatch(
                    image_id=image_id,
                    catalog_type=cat_type,
                    catalog_designation=desig,
                    angular_separation_degrees=row.dist,
                    is_in_field=True,
                    match_source="AUTOMATIC",
                    confidence_score=1.0 - (row.dist / 5.0)
                )
                self.session.add(match)
                count += 1
        
        self.session.commit()
        return count

    def _find_messier_in_field(self, ra: float, dec: float, radius: float):
        query = text("""
            SELECT designation, 
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM messier_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
        """)
        radius_meters = radius * 111320
        result = self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    def _find_ngc_in_field(self, ra: float, dec: float, radius: float):
        query = text("""
            SELECT designation,
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM ngc_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
            LIMIT 50
        """)
        radius_meters = radius * 111320
        result = self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    def _find_named_stars_in_field(self, ra: float, dec: float, radius: float):
        query = text("""
            SELECT designation,
                   ST_Distance(
                       location, 
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 111320.0 as dist
            FROM named_star_catalog
            WHERE ST_DWithin(
                location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
            LIMIT 50
        """)
        radius_meters = radius * 111320
        result = self.session.execute(query, {
            "ra": ra, 
            "dec": dec, 
            "radius_meters": radius_meters
        })
        return result.fetchall()

    def _construct_wcs(self, image: Image):
        """
        Construct WCS from image metadata (synchronous).
        Returns WCS object or None if construction fails.
        """
        try:
            from astropy.wcs import WCS
            
            # Require all essential parameters
            if not all(v is not None for v in [
                image.ra_center_degrees,
                image.dec_center_degrees,
                image.pixel_scale_arcsec,
                image.width_pixels,
                image.height_pixels
            ]):
                return None
            
            wcs = WCS(naxis=2)
            wcs.wcs.crpix = [image.width_pixels / 2.0, image.height_pixels / 2.0]
            wcs.wcs.crval = [image.ra_center_degrees, image.dec_center_degrees]
            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            
            # Convert arcsec/pixel to deg/pixel
            scale = image.pixel_scale_arcsec / 3600.0
            
            # Handle rotation
            rot_raw = image.rotation_degrees or 0.0
            rad = math.radians(rot_raw)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            
            # Get parity from header (defaults to 1 for Normal)
            parity = 1
            if image.raw_header and isinstance(image.raw_header, dict):
                parity = image.raw_header.get('astrometry_parity', 1)
            
            # Scale logic (Web Standard - Top Left Origin)
            s_x = -scale * parity
            s_y = -scale
            
            wcs.wcs.cd = [
                [s_x * cos_a, -s_y * sin_a],
                [s_x * sin_a, s_y * cos_a]
            ]
            
            # Verify WCS is valid
            if not wcs.is_celestial:
                return None
                
            return wcs
            
        except Exception as e:
            # Log error but continue without WCS validation
            print(f"Failed to construct WCS for image {image.id}: {e}")
            return None

    def _get_catalog_coords(self, cat_type: CatalogType, designation: str) -> Optional[Tuple[float, float]]:
        """
        Fetch RA/Dec coordinates for a catalog object (synchronous).
        Returns (ra, dec) tuple or None if not found.
        """
        try:
            if cat_type == CatalogType.MESSIER:
                result = self.session.execute(
                    select(MessierCatalog).where(MessierCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
            elif cat_type == CatalogType.NGC or cat_type == CatalogType.IC:
                result = self.session.execute(
                    select(NGCCatalog).where(NGCCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
            elif cat_type == CatalogType.NAMED_STAR:
                # Try direct match first
                result = self.session.execute(
                    select(NamedStarCatalog).where(NamedStarCatalog.designation == designation)
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                
                # Try normalized match (no spaces)
                from sqlalchemy import func
                result = self.session.execute(
                    select(NamedStarCatalog).where(
                        func.replace(NamedStarCatalog.designation, ' ', '') == designation.replace(' ', '')
                    )
                )
                obj = result.scalar_one_or_none()
                if obj:
                    return (obj.ra_degrees, obj.dec_degrees)
                    
        except Exception as e:
            print(f"Error fetching coordinates for {cat_type} {designation}: {e}")
            
        return None

    def _is_in_image_bounds(self, wcs, ra: float, dec: float, width: int, height: int) -> bool:
        """
        Check if celestial coordinates fall within image bounds.
        Uses same margin as images.py (100 pixels).
        """
        try:
            x, y = wcs.world_to_pixel_values(ra, dec)
            
            margin = 100
            if -margin <= x <= width + margin and -margin <= y <= height + margin:
                return True
                
        except Exception as e:
            # If transformation fails, exclude the object
            print(f"WCS transformation failed for RA={ra}, Dec={dec}: {e}")
            
        return False
