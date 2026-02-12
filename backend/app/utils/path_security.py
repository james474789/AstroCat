"""
Path Security Utilities
Provides secure path validation to prevent path traversal attacks.
"""

import os
from pathlib import Path
from typing import List, Union


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
