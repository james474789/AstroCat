
import time
import sys
import os
from sqlalchemy import text
from datetime import datetime

# Add parent directory to path
sys.path.append(os.getcwd())

from app.database import SessionLocal

def monitor_rate():
    session = SessionLocal()
    query = text("""
        SELECT COUNT(*) FROM images WHERE thumbnail_path IN (
            SELECT thumbnail_path 
            FROM images 
            WHERE thumbnail_path IS NOT NULL 
            GROUP BY thumbnail_path 
            HAVING COUNT(*) > 1
        );
    """)
    
    print("Monitoring thumbnail collision fixes...")
    print("Press Ctrl+C to stop.")
    print("-" * 60)
    print(f"{'Time':<10} | {'Collisions Left':<15} | {'Fixed':<10} | {'Rate (img/s)':<15}")
    print("-" * 60)
    
    last_count = None
    last_time = None
    
    try:
        while True:
            current_time = time.time()
            result = session.execute(query)
            current_count = result.scalar()
            
            if last_count is not None:
                delta_count = last_count - current_count
                delta_time = current_time - last_time
                
                # If negative, something added more collisions? Or just noise.
                if delta_time > 0:
                    rate = delta_count / delta_time
                    fixed = delta_count
                else:
                    rate = 0
                    fixed = 0
                
                time_str = datetime.now().strftime("%I:%M:%S")
                print(f"{time_str:<10} | {current_count:<15} | {fixed:<10} | {rate:<15.2f}")
            else:
                last_count = current_count
                time_str = datetime.now().strftime("%I:%M:%S")
                print(f"{time_str:<10} | {current_count:<15} | {'-':<10} | {'-':<15}")
            
            last_count = current_count
            last_time = current_time
            
            time.sleep(30)
            
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
    finally:
        session.close()

if __name__ == "__main__":
    monitor_rate()
