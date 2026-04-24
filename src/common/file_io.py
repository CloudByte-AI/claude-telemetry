"""
File I/O Utilities for CloudByte

Provides safe file operations with:
- Error handling
- JSON read/write with validation
- Directory creation with proper permissions
- Path resolution
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

from src.common.logging import get_logger


logger = get_logger(__name__)


def ensure_file(file_path: Path, create_dirs: bool = True) -> Path:
    """
    Ensure a file exists, creating it and parent directories if needed.

    Args:
        file_path: Path to the file
        create_dirs: Whether to create parent directories

    Returns:
        Path: The file path
    """
    if create_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    if not file_path.exists():
        file_path.touch()
        logger.debug(f"Created file: {file_path}")

    return file_path


def safe_write(
    file_path: Path,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> None:
    """
    Safely write content to a file.

    Args:
        file_path: Path to the file
        content: Content to write
        encoding: File encoding
        create_dirs: Whether to create parent directories
    """
    try:
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)

        logger.debug(f"Wrote content to {file_path}")
    except Exception as e:
        logger.error(f"Failed to write to {file_path}: {e}")
        raise


def safe_read(
    file_path: Path,
    encoding: str = "utf-8",
    default: Optional[str] = None,
) -> Optional[str]:
    """
    Safely read content from a file.

    Args:
        file_path: Path to the file
        encoding: File encoding
        default: Default value if file doesn't exist

    Returns:
        Optional[str]: File content or default value
    """
    try:
        if not file_path.exists():
            logger.debug(f"File not found: {file_path}")
            return default

        with open(file_path, "r", encoding=encoding) as f:
            content = f.read()

        logger.debug(f"Read content from {file_path}")
        return content
    except Exception as e:
        logger.error(f"Failed to read from {file_path}: {e}")
        return default


def write_json(
    file_path: Path,
    data: Any,
    indent: int = 2,
    create_dirs: bool = True,
    ensure_ascii: bool = False,
) -> None:
    """
    Write data to a JSON file.

    Args:
        file_path: Path to the JSON file
        data: Data to serialize
        indent: JSON indentation
        create_dirs: Whether to create parent directories
        ensure_ascii: Whether to escape non-ASCII characters
    """
    try:
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)

        logger.debug(f"Wrote JSON to {file_path}")
    except Exception as e:
        logger.error(f"Failed to write JSON to {file_path}: {e}")
        raise


def read_json(
    file_path: Path,
    default: Optional[Any] = None,
) -> Optional[Any]:
    """
    Read data from a JSON file.

    Args:
        file_path: Path to the JSON file
        default: Default value if file doesn't exist or is invalid

    Returns:
        Optional[Any]: Parsed JSON data or default value
    """
    try:
        if not file_path.exists():
            logger.debug(f"JSON file not found: {file_path}")
            return default

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.debug(f"Read JSON from {file_path}")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        return default
    except Exception as e:
        logger.error(f"Failed to read JSON from {file_path}: {e}")
        return default


def resolve_path(path: Path, base_path: Optional[Path] = None) -> Path:
    """
    Resolve a path to an absolute path.

    Args:
        path: Path to resolve (can be relative or absolute)
        base_path: Base path for relative paths (defaults to current directory)

    Returns:
        Path: Resolved absolute path
    """
    if path.is_absolute():
        return path

    if base_path is None:
        base_path = Path.cwd()

    return (base_path / path).resolve()


def get_file_size(file_path: Path) -> int:
    """
    Get the size of a file in bytes.

    Args:
        file_path: Path to the file

    Returns:
        int: File size in bytes (0 if file doesn't exist)
    """
    try:
        return file_path.stat().st_size
    except Exception:
        return 0


def file_exists(file_path: Path) -> bool:
    """
    Check if a file exists.

    Args:
        file_path: Path to check

    Returns:
        bool: True if file exists and is a file
    """
    return file_path.is_file()


def dir_exists(dir_path: Path) -> bool:
    """
    Check if a directory exists.

    Args:
        dir_path: Path to check

    Returns:
        bool: True if directory exists and is a directory
    """
    return dir_path.is_dir()


def delete_file(file_path: Path, missing_ok: bool = True) -> bool:
    """
    Delete a file.

    Args:
        file_path: Path to the file
        missing_ok: Whether to ignore if file doesn't exist

    Returns:
        bool: True if deleted successfully
    """
    try:
        file_path.unlink(missing_ok=missing_ok)
        logger.debug(f"Deleted file: {file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete {file_path}: {e}")
        return False


def delete_dir(dir_path: Path, recursive: bool = False) -> bool:
    """
    Delete a directory.

    Args:
        dir_path: Path to the directory
        recursive: Whether to delete recursively

    Returns:
        bool: True if deleted successfully
    """
    try:
        if recursive:
            import shutil
            shutil.rmtree(dir_path)
        else:
            dir_path.rmdir()

        logger.debug(f"Deleted directory: {dir_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete directory {dir_path}: {e}")
        return False


def list_files(
    dir_path: Path,
    pattern: str = "*",
    recursive: bool = False,
) -> list[Path]:
    """
    List files in a directory.

    Args:
        dir_path: Path to the directory
        pattern: Glob pattern for filtering files
        recursive: Whether to search recursively

    Returns:
        list[Path]: List of file paths
    """
    try:
        if recursive:
            files = list(dir_path.rglob(pattern))
        else:
            files = list(dir_path.glob(pattern))

        # Filter to only files (not directories)
        return [f for f in files if f.is_file()]
    except Exception as e:
        logger.error(f"Failed to list files in {dir_path}: {e}")
        return []
