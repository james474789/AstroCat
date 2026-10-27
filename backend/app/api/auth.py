"""
Authentication Router
Endpoints for login, logout, and user information.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from fastapi.responses import RedirectResponse
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserLogin, UserResponse
from app.services import auth_service
from app.api.dependencies import get_current_user_optional, get_current_user
from app.schemas.user import UserCreate
from app.config import settings

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def _set_csrf_cookie(response: Response) -> str:
    """Generate a CSRF token, set it as a cookie, and return the token."""
    token = secrets.token_urlsafe(32)
    if settings.csrf_enabled:
        response.set_cookie(
            key=settings.csrf_cookie_name,
            value=token,
            httponly=False,
            secure=settings.csrf_cookie_secure,
            samesite=settings.csrf_cookie_samesite,
            max_age=settings.csrf_cookie_max_age,
        )
        response.headers[settings.csrf_header_name] = token
    return token

@router.post("/login", response_model=UserResponse)
@limiter.limit("5/15minutes")
async def login(
    request: Request,
    login_data: UserLogin,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user and set HTTP-only JWT cookie.
    Rate limited to 5 attempts per 15 minutes to prevent brute force attacks.
    """
    # Find user
    result = await db.execute(select(User).where(User.email == login_data.email))
    user = result.scalar_one_or_none()
    
    if not user or not auth_service.verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )
    
    # Create JWT
    access_token = auth_service.create_access_token(data={"sub": user.email})
    
    # Set secure cookie
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.cookie_max_age,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )

    # Set CSRF cookie/header for subsequent requests
    _set_csrf_cookie(response)
    
    return user

@router.post("/logout")
async def logout(response: Response):
    """
    Clear authentication cookie.
    """
    response.delete_cookie(
        key="access_token",
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )
    if settings.csrf_enabled:
        response.delete_cookie(
            key=settings.csrf_cookie_name,
            samesite=settings.csrf_cookie_samesite,
            secure=settings.csrf_cookie_secure,
        )
    return {"message": "Successfully logged out"}

@router.get("/me", response_model=UserResponse)
async def get_me(response: Response, user: User = Depends(get_current_user)):
    """
    Get current user information based on cookie.
    Requires authentication.
    Also refreshes CSRF token.
    """
    # Refresh CSRF token to ensure it's always available
    _set_csrf_cookie(response)
    return user

@router.get("/setup-status")
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    """
    Check if at least one admin exists.
    Used by frontend to decide whether to show setup page.
    """
    result = await db.execute(select(User).where(User.is_admin == True))
    admin_exists = result.scalars().first() is not None
    return {"setup_complete": admin_exists, "desktop_mode": settings.desktop_mode}


# D1: the desktop app has one local user and no login screen. The Tauri shell opens
# /api/auth/desktop-login?nonce=<per-launch secret from the launcher>, which signs that user in.
DESKTOP_USER_EMAIL = "astrocat@localhost"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@router.get("/desktop-login")
async def desktop_login(request: Request, nonce: str = "", db: AsyncSession = Depends(get_db)):
    """
    Sign in the local desktop user and redirect to the app.
    Only exists in desktop mode, only from loopback, and only with the launcher's nonce.
    """
    if not settings.desktop_mode or not settings.desktop_launch_nonce:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK_HOSTS or not secrets.compare_digest(
        nonce.encode(), settings.desktop_launch_nonce.encode()
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # The first active admin is the desktop user; create it on first launch.
    result = await db.execute(select(User).where(User.is_admin == True, User.is_active == True).order_by(User.id))
    user = result.scalars().first()
    if not user:
        user = User(
            email=DESKTOP_USER_EMAIL,
            # never typed: the desktop app signs in with the nonce, not a password
            hashed_password=auth_service.get_password_hash(secrets.token_urlsafe(32)),
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    access_token = auth_service.create_access_token(data={"sub": user.email})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.cookie_max_age,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )
    _set_csrf_cookie(response)
    return response

@router.post("/admin-sign-up", response_model=UserResponse)
@limiter.limit("3/hour")
async def admin_sign_up(
    request: Request,
    user_data: UserCreate,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Initial admin registration.
    Only works if no users exist in the database.
    Rate limited to 3 attempts per hour to prevent abuse.
    """
    if settings.desktop_mode:
        # the desktop user is created by /desktop-login; no other account can be set up
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The desktop app signs in automatically."
        )

    # Check if any user exists
    result = await db.execute(select(User))
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup already completed. Please log in or contact an administrator."
        )
    
    # Create the first user as an admin
    new_user = User(
        email=user_data.email,
        hashed_password=auth_service.get_password_hash(user_data.password),
        is_admin=True,
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Auto-login
    access_token = auth_service.create_access_token(data={"sub": new_user.email})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.cookie_max_age,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )

    # Set CSRF cookie/header for subsequent requests
    _set_csrf_cookie(response)
    
    return new_user
