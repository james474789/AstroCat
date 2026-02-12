from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
import os
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.image import Image
from app.utils.path_security import validate_path_safety

router = APIRouter()

class FileEntry(BaseModel):
    name: str
    path: str
    type: str  # 'directory' or 'file'
    has_children: bool = False
    image_count: int = 0

@router.get("/list", response_model=List[FileEntry])
async def list_directory(
    path: Optional[str] = Query(None, description="Path to list"),
    db: AsyncSession = Depends(get_db)
):
    """
    List contents of a directory. 
    Restricted to configured image_paths.
    """
    allowed_paths = []
    for p in settings.image_paths_list:
        try:
            resolved = Path(p).resolve()
            allowed_paths.append(resolved)
        except Exception as e:
            print(f"⚠️ Error resolving path {p}: {e}")
    
    if not path:
        # Return mount points
        # Fetch friendly names from Redis settings
        from app.api.settings import get_settings as get_system_settings
        try:
            system_settings = get_system_settings()
            friendly_names = system_settings.mount_friendly_names
        except Exception:
            friendly_names = {}

        entries = []
        for p in allowed_paths:
            p_str = str(p)
            exists = False
            is_dir = False
            try:
                exists = p.exists()
                if exists:
                    is_dir = p.is_dir()
            except Exception as e:
                print(f"⚠️ Error checking path {p_str}: {e}")

            if exists and is_dir:
                # Count images in this mount point
                # Make prefix matching more robust (handle path separators)
                prefix = p_str if p_str.endswith('/') or p_str.endswith('\\') else p_str + os.sep
                
                # Check for images starting with this path
                # We handle both / and \ in the database if necessary
                alt_prefix = prefix.replace('/', '\\') if '/' in prefix else prefix.replace('\\', '/')
                
                count_stmt = select(func.count()).where(
                    Image.file_path.startswith(prefix) | 
                    Image.file_path.startswith(alt_prefix)
                )
                count = await db.scalar(count_stmt) or 0
                
                # Use friendly name if available
                display_name = friendly_names.get(p_str) or p.name or p_str

                entries.append(FileEntry(
                    name=display_name,
                    path=p_str,
                    type="directory",
                    has_children=True,
                    image_count=count
                ))
            else:
                print(f"ℹ️ Path skipped (exists={exists}, is_dir={is_dir}): {p_str}")
        
        return entries

    # Validate path using secure path validation
    allowed_paths = [Path(p).resolve() for p in settings.image_paths_list]
    
    if not validate_path_safety(path, allowed_paths):
        raise HTTPException(
            status_code=403, 
            detail="Access denied: Path not within allowed directories or contains unsafe elements"
        )
    
    target_path = Path(path).resolve(strict=True)
        
    if not target_path.exists() or not target_path.is_dir():
         raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    try:
        # Sort directories first, then files
        with os.scandir(target_path) as it:
            obj_list = list(it)
            
        # Sort by name
        obj_list.sort(key=lambda x: x.name.lower())
        
        for entry in obj_list:
            if entry.name.startswith('.'):
                 continue
                 
            if entry.is_dir():
                # Count images in this subdirectory (recursive)
                prefix = entry.path if entry.path.endswith('/') else entry.path + '/'
                count_stmt = select(func.count()).where(Image.file_path.startswith(prefix))
                count = await db.scalar(count_stmt) or 0
                
                entries.append(FileEntry(
                    name=entry.name,
                    path=entry.path,
                    type="directory",
                    has_children=True,
                    image_count=count
                ))
            # We could list files too if needed, but for "Navigation" usually just folders?
            # User said "navigate the folder tree". usually that implies folders. 
            # But the resulting filter is likely on the folder level.
            # Let's include subdirectories ONLY for the tree navigation to be cleaner.
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return entries
