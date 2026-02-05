import csv
import os
import sys
import asyncio
import traceback
from pathlib import Path
from sqlalchemy import text, insert
from app.database import AsyncSessionLocal, engine, Base
from app.models.catalog import NamedStarCatalog

def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

async def seed_named_stars():
    log("Starting seed_named_stars REVISED...")
    
    # Path to the data directory
    script_dir = Path(__file__).parent
    csv_path = script_dir.parent.parent / "data" / "NamedStars.csv"
    
    if not csv_path.exists():
        log(f"CSV not found at {csv_path}. Trying absolute container path...")
        csv_path = Path("/app/data/NamedStars.csv")
    
    if not os.path.exists(csv_path):
        log(f"ERROR: CSV not found at {csv_path}")
        return

    log(f"Reading from {csv_path}...")
    
    objects = []
    
    seen_designations = set()
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                desig = row.get("id")
                if not desig:
                    continue
                
                # Deduplicate
                if desig in seen_designations:
                    log(f"Skipping duplicate: {desig}")
                    continue
                seen_designations.add(desig)
                    
                ra = float(row.get("alpha") or 0)
                dec = float(row.get("delta") or 0)
                mag_str = row.get("magnitude")
                mag = float(mag_str) if mag_str else None
                
                hip = row.get("HIP")
                if hip and not hip.strip(): hip = None
                
                hd = row.get("HD")
                if hd and not hd.strip(): hd = None
                
                common = row.get("Common name")
                if common and not common.strip(): common = None

                # Create dictionary for Core Insert
                obj_dict = {
                    "designation": desig,
                    "common_name": common,
                    "ra_degrees": ra,
                    "dec_degrees": dec,
                    "magnitude": mag,
                    "spectral_type": row.get("Spectral type"),
                    "hd_id": hd,
                    "hip_id": hip,
                    "location": None # Will be updated via PostGIS
                }
                objects.append(obj_dict)
    except Exception as e:
        log(f"Error reading CSV: {e}")
        return

    log(f"Found {len(objects)} stars. Ensuring table exists...")
    
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log("Tables created/verified.")
    except Exception as e:
        log(f"Error creating tables: {e}")
        traceback.print_exc(file=sys.stderr)
        return

    async with AsyncSessionLocal() as session:
        # Check if already seeded to avoid unnecessary work
        try:
            r = await session.execute(text("SELECT count(*) FROM named_star_catalog"))
            current_count = r.scalar() or 0
            if current_count >= 3500:
                log(f"⏩ Named stars already seeded ({current_count}). Skipping.")
                return current_count
        except Exception:
            pass # Table might not exist yet
        
        log(f"Inserting {len(objects)} stars via Core Insert...")
        
        batch_size = 1000
        try:
            for i in range(0, len(objects), batch_size):
                batch = objects[i:i+batch_size]
                if not batch: break
                
                stmt = insert(NamedStarCatalog)
                await session.execute(stmt, batch)
                await session.commit()
                log(f"Inserted batch {i//batch_size + 1} ({len(batch)} items)")
        except Exception as e:
            log(f"Batch insert failed!")
            traceback.print_exc(file=sys.stderr)
            await session.rollback()
            return
            
        log("Updating PostGIS locations...")
        try:
            await session.execute(text("""
                UPDATE named_star_catalog 
                SET location = ST_SetSRID(ST_MakePoint(ra_degrees, dec_degrees), 4326)::geography
                WHERE location IS NULL
            """))
            await session.commit()
            log("PostGIS locations updated.")
        except Exception as e:
            log(f"PostGIS update failed: {e}")
            traceback.print_exc(file=sys.stderr)
        
        try:
            r = await session.execute(text("SELECT count(*) FROM named_star_catalog"))
            final_count = r.scalar()
            log(f"Final Count: {final_count}")
        except Exception as e:
            log(f"Verify failed: {e}")
            
    log("Done!")

if __name__ == "__main__":
    asyncio.run(seed_named_stars())
