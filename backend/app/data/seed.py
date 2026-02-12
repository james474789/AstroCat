"""
Database Seeding Script
Populates the Messier and NGC catalogs with astronomical data.
"""

import asyncio
import json
import csv
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, engine
from app.models.catalog import MessierCatalog, NGCCatalog


async def seed_messier_catalog(session: AsyncSession) -> int:
    """Seed the Messier catalog table from Messier.csv."""
    count = 0
    
    # 1. Load JSON data for metadata (type, constellation) fallback
    # We use this because Messier.csv is missing these fields
    json_path = Path(__file__).parent.parent.parent / "data" / "messier_catalog.json"
    metadata_map = {}
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                for item in json_data:
                    # Map M-number (int) to data
                    if "messier_number" in item:
                        metadata_map[item["messier_number"]] = item
                    # Also map designation
                    if "designation" in item:
                        metadata_map[item["designation"]] = item
        except Exception as e:
            print(f"⚠️ Error reading JSON metadata: {e}")

    # 2. Load CSV data
    # Messier.csv is located in backend/data/
    csv_path = Path(__file__).parent.parent.parent / "data" / "Messier.csv"
    
    if not csv_path.exists():
        print(f"⚠️ Messier.csv not found at {csv_path}")
        return 0

    print(f"📄 Importing Messier catalog from {csv_path}...")
    
    import csv
    with open(csv_path, 'r', encoding='utf-8') as f:
        # The CSV header is: id,alpha,delta,magnitude,diameter,axisRatio,posAngle,Common name,NGC/IC,PGC
        reader = csv.DictReader(f)
        
        for row in reader:
            designation = row["id"] # e.g. M1
            
            # Extract number
            try:
                m_num = int(designation[1:])
            except:
                continue
                
            # Get metadata from JSON if available
            meta = metadata_map.get(m_num) or metadata_map.get(designation) or {}
            
            # Prepare values
            common_name = row.get("Common name") or meta.get("common_name")
            ngc = row.get("NGC/IC") or meta.get("ngc")
            obj_type = meta.get("type", "Deep Sky Object")
            constellation = meta.get("const")
            
            # Safe float conversion
            def get_float(k):
                val = row.get(k)
                if val and val.strip():
                    try:
                        return float(val)
                    except:
                        return None
                return None

            ra = get_float("alpha")
            dec = get_float("delta")
            mag = get_float("magnitude")
            diam =  row.get("diameter") # Keep as string for now if just simple number
            axis_ratio = get_float("axisRatio")
            pos_angle = get_float("posAngle")
            pgc = row.get("PGC")

            if ra is None or dec is None:
                print(f"Skipping {designation} due to missing coordinates")
                continue

            # Check if exists to update
            existing = await session.execute(
                text("SELECT id FROM messier_catalog WHERE designation = :des"),
                {"des": designation}
            )
            existing_row = existing.fetchone()

            if existing_row:
                # Update
                await session.execute(
                    text("""
                        UPDATE messier_catalog SET
                            messier_number = :m_num,
                            common_name = :common,
                            ngc_designation = :ngc,
                            ra_degrees = :ra,
                            dec_degrees = :dec,
                            object_type = :otype,
                            constellation = :const,
                            apparent_magnitude = :mag,
                            angular_size_arcmin = :diam,
                            axis_ratio = :ar,
                            position_angle = :pa,
                            pgc_designation = :pgc
                        WHERE designation = :des
                    """),
                    {
                        "m_num": m_num,
                        "common": common_name,
                        "ngc": ngc,
                        "ra": ra,
                        "dec": dec,
                        "otype": obj_type,
                        "const": constellation,
                        "mag": mag,
                        "diam": diam,
                        "ar": axis_ratio,
                        "pa": pos_angle,
                        "pgc": pgc,
                        "des": designation
                    }
                )
            else:
                # Insert
                obj = MessierCatalog(
                    messier_number=m_num,
                    designation=designation,
                    common_name=common_name,
                    ngc_designation=ngc,
                    ra_degrees=ra,
                    dec_degrees=dec,
                    object_type=obj_type,
                    constellation=constellation,
                    apparent_magnitude=mag,
                    angular_size_arcmin=diam,
                    axis_ratio=axis_ratio,
                    position_angle=pos_angle,
                    pgc_designation=pgc
                )
                session.add(obj)
            
            count += 1

    await session.commit()
    
    # Update PostGIS locations
    await session.execute(text("""
        UPDATE messier_catalog 
        SET location = ST_SetSRID(ST_MakePoint(ra_degrees, dec_degrees), 4326)::geography
        WHERE location IS NULL OR ra_degrees != ST_X(location::geometry) OR dec_degrees != ST_Y(location::geometry)
    """))
    await session.commit()
    
    return count


async def seed_ngc_catalog_sample(session: AsyncSession) -> int:
    """Seed a sample of NGC objects (popular ones that aren't in Messier)."""
    
    NGC_SAMPLE = [
        {"ngc_number": 7000, "designation": "NGC 7000", "common_name": "North America Nebula", "ra_degrees": 314.75, "dec_degrees": 44.37, "object_type": "Emission Nebula", "constellation": "Cygnus"},
        {"ngc_number": 7293, "designation": "NGC 7293", "common_name": "Helix Nebula", "ra_degrees": 337.41, "dec_degrees": -20.84, "object_type": "Planetary Nebula", "constellation": "Aquarius"},
        {"ngc_number": 2237, "designation": "NGC 2237", "common_name": "Rosette Nebula", "ra_degrees": 97.97, "dec_degrees": 4.95, "object_type": "Emission Nebula", "constellation": "Monoceros"},
        {"ngc_number": 6992, "designation": "NGC 6992", "common_name": "Veil Nebula (East)", "ra_degrees": 312.75, "dec_degrees": 31.72, "object_type": "Supernova Remnant", "constellation": "Cygnus"},
        {"ngc_number": 869, "designation": "NGC 869", "common_name": "Double Cluster (h)", "ra_degrees": 34.75, "dec_degrees": 57.13, "object_type": "Open Cluster", "constellation": "Perseus"},
        {"ngc_number": 884, "designation": "NGC 884", "common_name": "Double Cluster (χ)", "ra_degrees": 35.08, "dec_degrees": 57.15, "object_type": "Open Cluster", "constellation": "Perseus"},
        {"ngc_number": 2024, "designation": "NGC 2024", "common_name": "Flame Nebula", "ra_degrees": 85.42, "dec_degrees": -1.85, "object_type": "Emission Nebula", "constellation": "Orion"},
        {"ngc_number": 2359, "designation": "NGC 2359", "common_name": "Thor's Helmet", "ra_degrees": 109.27, "dec_degrees": -13.22, "object_type": "Emission Nebula", "constellation": "Canis Major"},
        {"ngc_number": 6888, "designation": "NGC 6888", "common_name": "Crescent Nebula", "ra_degrees": 303.06, "dec_degrees": 38.35, "object_type": "Emission Nebula", "constellation": "Cygnus"},
        {"ngc_number": 253, "designation": "NGC 253", "common_name": "Sculptor Galaxy", "ra_degrees": 11.89, "dec_degrees": -25.29, "object_type": "Spiral Galaxy", "constellation": "Sculptor"},
        {"ngc_number": 891, "designation": "NGC 891", "common_name": "Silver Sliver Galaxy", "ra_degrees": 35.6371, "dec_degrees": 42.3492, "object_type": "Spiral Galaxy", "constellation": "Andromeda"},
        {"ngc_number": 6960, "designation": "NGC 6960", "common_name": "Veil Nebula (West)", "ra_degrees": 311.41, "dec_degrees": 30.71, "object_type": "Supernova Remnant", "constellation": "Cygnus"},
        {"ngc_number": 281, "designation": "NGC 281", "common_name": "Pacman Nebula", "ra_degrees": 13.05, "dec_degrees": 56.61, "object_type": "Emission Nebula", "constellation": "Cassiopeia"},
        {"ngc_number": 7635, "designation": "NGC 7635", "common_name": "Bubble Nebula", "ra_degrees": 345.12, "dec_degrees": 61.20, "object_type": "Emission Nebula", "constellation": "Cassiopeia"},
        {"ngc_number": 2244, "designation": "NGC 2244", "common_name": "Satellite Cluster", "ra_degrees": 97.98, "dec_degrees": 4.98, "object_type": "Open Cluster", "constellation": "Monoceros"},
        {"ngc_number": 1499, "designation": "NGC 1499", "common_name": "California Nebula", "ra_degrees": 60.91, "dec_degrees": 36.41, "object_type": "Emission Nebula", "constellation": "Perseus"},
    ]
    
    count = 0
    for data in NGC_SAMPLE:
        existing = await session.execute(
            text("SELECT id FROM ngc_catalog WHERE designation = :des"),
            {"des": data["designation"]}
        )
        if existing.fetchone():
            continue
        
        obj = NGCCatalog(
            ngc_number=data["ngc_number"],
            designation=data["designation"].replace(" ", ""),
            common_name=data.get("common_name"),
            ra_degrees=data["ra_degrees"],
            dec_degrees=data["dec_degrees"],
            object_type=data.get("object_type"),
            constellation=data.get("constellation"),
        )
        session.add(obj)
        count += 1
    
    await session.commit()
    
    # Update PostGIS locations
    await session.execute(text("""
        UPDATE ngc_catalog 
        SET location = ST_SetSRID(ST_MakePoint(ra_degrees, dec_degrees), 4326)::geography
        WHERE location IS NULL
    """))
    await session.commit()
    
    return count


async def seed_ngc_from_csv(session: AsyncSession, csv_path: str) -> int:
    """Seed the NGC catalog from a CSV file."""
    print(f"📄 Importing NGC catalog from {csv_path}...")
    
    def parse_ra(ra_str):
        if not ra_str: return 0.0
        try:
            parts = ra_str.split(':')
            h = float(parts[0])
            m = float(parts[1]) if len(parts) > 1 else 0.0
            s = float(parts[2]) if len(parts) > 2 else 0.0
            return (h + m/60.0 + s/3600.0) * 15.0
        except Exception: return 0.0

    def parse_dec(dec_str):
        if not dec_str: return 0.0
        try:
            sign = 1.0
            if dec_str.startswith('-'):
                sign = -1.0
                dec_str = dec_str[1:]
            elif dec_str.startswith('+'):
                dec_str = dec_str[1:]
            parts = dec_str.split(':')
            d = float(parts[0])
            m = float(parts[1]) if len(parts) > 1 else 0.0
            s = float(parts[2]) if len(parts) > 2 else 0.0
            return sign * (d + m/60.0 + s/3600.0)
        except Exception: return 0.0

    def safe_float(val):
        if not val: return None
        try: return float(val)
        except: return None

    count = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            name = row['Name']
            
            # Check if exists
            existing = await session.execute(
                text("SELECT id FROM ngc_catalog WHERE designation = :des"),
                {"des": name}
            )
            if existing.fetchone():
                continue

            # Extract number
            try:
                num_val = 0
                if name.startswith('NGC'):
                    num_str = name[3:].lstrip('0')
                    num_val = int(num_str) if num_str else 0
                elif name.startswith('IC'):
                    num_str = name[2:].lstrip('0')
                    num_val = int(num_str) if num_str else 0
            except: num_val = 0

            obj = NGCCatalog(
                designation=name.replace(" ", ""),
                ngc_number=num_val,
                common_name=row.get('Common names'),
                messier_designation=f"M{row['M']}" if row.get('M') else None,
                ic_designation=f"IC{row['IC'].replace(' ', '')}" if row.get('IC') else None,
                ra_degrees=parse_ra(row['RA']),
                dec_degrees=parse_dec(row['Dec']),
                object_type=row.get('Type'),
                hubble_type=row.get('Hubble'),
                constellation=row.get('Const'),
                apparent_magnitude=safe_float(row.get('V-Mag')) or safe_float(row.get('B-Mag')),
                b_magnitude=safe_float(row.get('B-Mag')),
                surface_brightness=safe_float(row.get('SurfBr')),
                major_axis_arcmin=safe_float(row.get('MajAx')),
                minor_axis_arcmin=safe_float(row.get('MinAx')),
                position_angle=safe_float(row.get('PosAng')),
                redshift=safe_float(row.get('Redshift')),
                notes=row.get('OpenNGC notes')
            )
            session.add(obj)
            count += 1
            if count % 1000 == 0:
                await session.commit()
                print(f"  Imported {count} objects...")
        
        await session.commit()

    # Update PostGIS locations
    print("  Updating PostGIS location columns...")
    await session.execute(text("""
        UPDATE ngc_catalog 
        SET location = ST_SetSRID(ST_MakePoint(ra_degrees, dec_degrees), 4326)::geography
        WHERE location IS NULL
    """))
    await session.commit()
    
    return count


async def seed_all():
    """Run all seeding operations."""
    print("🌟 Seeding AstroCat catalogs...")
    
    async with AsyncSessionLocal() as session:
        # Check if already seeded to avoid unnecessary work if the DB is "full"
        res = await session.execute(text("SELECT count(*) FROM messier_catalog"))
        m_count = res.scalar() or 0
        res = await session.execute(text("SELECT count(*) FROM ngc_catalog"))
        n_count = res.scalar() or 0
        
        if m_count >= 110 and n_count >= 13000:
            print(f"⏩ Catalogs already appear to be seeded (Messier: {m_count}, NGC: {n_count}). Skipping.")
            return m_count + n_count

        # 1. Seed Messier catalog
        messier_count = await seed_messier_catalog(session)
        print(f"✅ Seeded {messier_count} Messier objects")
        
        # 2. Seed NGC catalog
        ngc_count = 0
        ngc_csv = Path(__file__).parent.parent.parent / "data" / "NGC.csv"
        if os.path.exists(ngc_csv):
            ngc_count = await seed_ngc_from_csv(session, str(ngc_csv))
            print(f"✅ Imported {ngc_count} NGC objects from CSV")
        else:
            ngc_count = await seed_ngc_catalog_sample(session)
            print(f"✅ Seeded {ngc_count} NGC objects from sample list")
    
    print("🎉 Catalog seeding complete!")
    return messier_count + ngc_count


if __name__ == "__main__":
    asyncio.run(seed_all())
