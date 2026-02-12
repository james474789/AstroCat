import asyncio
import sys
import os

# Add backend directory to path so we can import app modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import select
from app.database import engine, AsyncSessionLocal
from app.models.user import User
from app.services import auth_service

async def inject_test_user():
    print("Connecting to database...")
    async with AsyncSessionLocal() as session:
        # Check if user exists
        email = "perf_test@astrocat.com"
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            print(f"User {email} already exists.")
            if not user.is_admin:
                print("Promoting to admin...")
                user.is_admin = True
                await session.commit()
                print("Promoted.")
            return

        print(f"Creating user {email}...")
        password = "perf_test_123"
        hashed_password = auth_service.get_password_hash(password)
        
        new_user = User(
            email=email,
            hashed_password=hashed_password,
            is_admin=True,
            is_active=True
        )
        session.add(new_user)
        await session.commit()
        print(f"User {email} created successfully.")

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
             asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(inject_test_user())
    except Exception as e:
        print(f"Error: {e}")
