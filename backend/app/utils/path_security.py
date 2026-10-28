"""
Path Security Utilities
Provides secure path validation to prevent path traversal attacks.
"""

import os
import re
from pathlib import Path
from typing import List, Union

from sqlalchemy import or_

# Escape char for LIKE patterns built from paths. Postgres' default is a backslash, which
# Windows paths are full of, so every path LIKE names '!' explicitly.
LIKE_ESCAPE = "!"


def escape_like(value: str) -> str:
    """`value` with LIKE wildcards escaped, to match itself literally under ESCAPE '!'."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def root_prefix_pattern(root: str) -> tuple[str, str]:
    """
    (exact, like_pattern) matching a folder and everything under it, for LIKE ... ESCAPE '!'.

    Stored file_path values are str(Path(root) / ...), so a Windows root (drive letter or
    backslashes) is matched with backslashes throughout.
    """
    r = root.rstrip("/\\")
    sep = "\\" if ("\\" in r or re.match(r"^[A-Za-z]:", r)) else "/"
    if sep == "\\":
        r = r.replace("/", "\\")
    return r, escape_like(r) + sep + "%"


def path_under_root(column, root: str):
    """SQLAlchemy predicate: `column` is the folder `root` or a path inside it."""
    exact, pattern = root_prefix_pattern(root)
    return or_(column == exact, column.like(pattern, escape=LIKE_ESCAPE))


def path_startswith(column, prefix: str):
    """
    `column` starts with `prefix`, taken literally. Use instead of SQLAlchemy's startswith(),
    which leaves backslashes (Windows paths) and _/% in the prefix acting as LIKE syntax.
    """
    return column.like(escape_like(prefix) + "%", escape=LIKE_ESCAPE)


def validate_path_safety(
    target: Union[str, Path], 
    allowed_roots: List[Union[str, Path]],
    allow_symlinks: bool = False
) -> bool:
    """
    Validate that a path is safe to access.
    
    Args:
        target: The path to validate
        allowed_roots: List of allowed root directories
        allow_symlinks: Whether to allow symbolic links (default: False)
    
    Returns:
        True if the path is safe to access, False otherwise
    
    Security checks:
        - Path must exist
        - Path must resolve to a location within allowed roots
        - Symlinks are blocked by default
        - Handles path traversal attempts (../, etc.)
    """
    try:
        target_path = Path(target)
        
        # Resolve to absolute path (follows symlinks and resolves .., ., etc.)
        # strict=True means it will raise if path doesn't exist
        resolved = target_path.resolve(strict=True)
        
        # Block symlinks unless explicitly allowed
        if not allow_symlinks and target_path.is_symlink():
            return False
        
        # Check if resolved path is within any allowed root
        for root in allowed_roots:
            root_path = Path(root).resolve(strict=False)
            
            # Use os.path.commonpath to check if target is under root
            try:
                # Both paths must be absolute for commonpath
                common = os.path.commonpath([str(resolved), str(root_path)])
                
                # If the common path equals the root, target is within it
                if Path(common) == root_path:
                    return True
                    
            except ValueError:
                # Paths are on different drives (Windows) or incompatible
                continue
        
        return False
        
    except (OSError, RuntimeError, ValueError):
        # Path doesn't exist, permission denied, or other filesystem error
        return False


def is_within_roots(target: str, allowed_roots: List[Union[str, Path]]) -> bool:
    """
    Lexical check that ``target`` sits at or under one of ``allowed_roots``.

    Unlike validate_path_safety the path need not exist (a scan request can name a folder
    that is not mounted yet), so nothing is resolved on disk. Any ``..`` component is
    rejected outright. Works for POSIX container paths and Windows drive paths alike.
    """
    if not isinstance(target, str) or not target:
        return False
    if ".." in Path(target).parts or ".." in target.replace("\\", "/").split("/"):
        return False
    norm = os.path.normcase(os.path.normpath(target))
    for root in allowed_roots:
        root_norm = os.path.normcase(os.path.normpath(str(root)))
        try:
            if os.path.commonpath([norm, root_norm]) == root_norm:
                return True
        except ValueError:
            # different drives (Windows) or mixed absolute/relative
            continue
    return False


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize a filename to prevent directory traversal and other attacks.
    
    Args:
        filename: The filename to sanitize
        max_length: Maximum allowed length for the filename
    
    Returns:
        Sanitized filename safe for filesystem operations
    """
    # Remove any directory components
    filename = os.path.basename(filename)
    
    # Remove path separators that might have survived basename
    filename = filename.replace('/', '_').replace('\\', '_')
    
    # Remove null bytes
    filename = filename.replace('\0', '')
    
    # Remove leading/trailing dots and spaces
    filename = filename.strip('. ')
    
    # Truncate to max length
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        ext_len = len(ext)
        name = name[:max_length - ext_len]
        filename = name + ext
    
    # If filename is empty or dangerous, provide default
    if not filename or filename in ('.', '..'):
        filename = 'unnamed_file'
    
    return filename


def validate_file_path(
    file_path: Union[str, Path],
    allowed_roots: List[Union[str, Path]]
) -> Path:
    """
    Validate and return a safe file path.
    
    Args:
        file_path: The file path to validate
        allowed_roots: List of allowed root directories
    
    Returns:
        Validated Path object
    
    Raises:
        ValueError: If the path is unsafe or outside allowed roots
        FileNotFoundError: If the path doesn't exist
    """
    if not validate_path_safety(file_path, allowed_roots):
        raise ValueError(
            "Access denied: Path is outside allowed directories or contains unsafe elements"
        )
    
    path = Path(file_path).resolve(strict=True)
    
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {file_path}")
    
    return path
