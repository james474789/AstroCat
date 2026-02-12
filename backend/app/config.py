"""
AstroCat Configuration Module
Loads settings from environment variables with sensible defaults.
"""

from functools import lru_cache
from typing import List, Union, Any, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    app_name: str = "AstroCat"
    debug: bool = False

    # Authentication
    secret_key: str = Field(..., min_length=32)  # Required, no default
    access_token_expire_minutes: int = 60 * 24 * 7  # 1 week
    jwt_algorithm: str = "HS256"
    
    # Cookie Security
    cookie_secure: bool = False  # Set to True for HTTPS (production)
    cookie_max_age: int = 60 * 60 * 24 * 2  # 2 days (reduced from 7)
    cookie_samesite: str = "lax"  # lax, strict, or none
    
    # Rate Limiting
    rate_limit_enabled: bool = True  # Enable rate limiting
    rate_limit_login: str = "5/15minutes"  # Login attempts
    rate_limit_search: str = "30/minute"  # Search queries
    rate_limit_general: str = "100/minute"  # General API requests

    # CSRF Protection
    csrf_enabled: bool = True
    csrf_cookie_name: str = "csrf_token"
    csrf_header_name: str = "X-CSRF-Token"
    csrf_cookie_secure: bool = False  # Set to True in production with HTTPS
    csrf_cookie_samesite: str = "lax"
    csrf_cookie_max_age: int = 60 * 60 * 24 * 2  # 2 days
    
    # Database
    database_url: str = Field(..., alias="DATABASE_URL")
    
    # Redis
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    
    # Image Storage
    image_paths: str = "/data/images"
    thumbnail_cache_path: str = "/data/thumbnails"
    thumbnail_max_size: int = 400

    # Logging
    log_dir: str = "/var/log/astrocat"

    # Astrometry.net
    astrometry_api_key: str = ""
    local_astrometry_url: str = ""
    local_astrometry_api_key: str = ""

    # API Settings
    api_prefix: str = "/api"
    
    @field_validator('secret_key')
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Validate that the secret key is secure."""
        if not v or len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        
        # Check for common insecure values
        insecure_values = [
            "change-me-in-production",
            "secret",
            "password",
            "test",
            "dev",
            "default",
            "changeme",
        ]
        if v.lower() in insecure_values or any(bad in v.lower() for bad in insecure_values):
            raise ValueError(
                "SECRET_KEY appears to be insecure. Generate a secure key with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        
        return v
    
    
    @property
    def image_paths_list(self) -> List[str]:
        """Parse comma-separated image paths into a list."""
        return [p.strip() for p in self.image_paths.split(",") if p.strip()]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    try:
        return Settings()
    except Exception as e:
        print(f"❌ Configuration Error: {e}")
        # Always return a default settings object if it fails to load from env
        # to prevent complete crash of the worker/backend if possible, 
        # though this might lead to other issues later.
        # For now, let's just raise it but with the print above.
        raise e


# Convenience alias
settings = get_settings()
