"""
API Dependencies
Reusable FastAPI dependencies for endpoint protection.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User
from app.services import auth_service
from app.config import settings

async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User | None:
    """
    Extract user from JWT in HTTP-only cookie.
    Returns None if token is missing or invalid.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None
        
    payload = auth_service.decode_access_token(token)
    if not payload:
        return None
        
    email: str = payload.get("sub")
    if not email:
        return None
        
    # Find user in database
    result = await db.execute(select(User).where(User.email == email, User.is_active == True))
    user = result.scalar_one_or_none()
    
    return user

async def get_current_user(
    user: User | None = Depends(get_current_user_optional)
) -> User:
    """
    Dependency that enforces authentication.
    Always requires a valid user - use get_current_user_optional for public endpoints.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

async def require_admin(user: User = Depends(get_current_user)) -> User:
    """
    Dependency that enforces admin privileges.
    User must be authenticated (via get_current_user) and have admin flag.
    """
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return user
