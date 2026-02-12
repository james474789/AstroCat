"""
Create Admin User Script
Creates a default admin user if it does not already exist.
Usage: python -m app.scripts.create_admin
"""

import asyncio
import os
import sys
from sqlalchemy import select

# Add parent directory to path to allow running as module
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import auth_service
from app.utils.password_policy import validate_password

async def create_admin():
    async with AsyncSessionLocal() as db:
        # Check if admin user already exists
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        
        if admin:
            print("Admin user already exists.")
            return

        # Create new admin user
        # Require ADMIN_PASSWORD from env
        password = os.getenv("ADMIN_PASSWORD")
        if not password:
            print("ADMIN_PASSWORD is required to create the admin user.")
            return

        errors = validate_password(password)
        if errors:
            print("ADMIN_PASSWORD does not meet password policy:")
            for err in errors:
                print(f"- {err}")
            return
        hashed_password = auth_service.get_password_hash(password)
        
        new_admin = User(
            username="admin",
            hashed_password=hashed_password,
            is_active=True
        )
        
        db.add(new_admin)
        await db.commit()
        print(f"Successfully created admin user: admin")

if __name__ == "__main__":
    asyncio.run(create_admin())
