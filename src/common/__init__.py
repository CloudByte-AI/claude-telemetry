"""
Common utilities for CloudByte.

This module contains shared infrastructure code including logging,
path management, file I/O, and configuration.
"""

# Import individual modules for convenience
from src.common.logging import get_logger, setup_logging, CloudByteLogger, get_cloudbyte_logger
from src.common.paths import (
    get_home_dir,
    get_cloudbyte_dir,
    get_data_dir,
    get_logs_dir,
    get_db_path,
    get_log_file,
    ensure_directories,
    get_config_file,
)
from src.common.file_io import (
    ensure_file,
    safe_write,
    safe_read,
    write_json,
    read_json,
    resolve_path,
    get_file_size,
    file_exists,
    dir_exists,
    delete_file,
    delete_dir,
    list_files,
)

__all__ = [
    # Logging
    "get_logger",
    "setup_logging",
    "CloudByteLogger",
    "get_cloudbyte_logger",
    # Paths
    "get_home_dir",
    "get_cloudbyte_dir",
    "get_data_dir",
    "get_logs_dir",
    "get_db_path",
    "get_log_file",
    "ensure_directories",
    "get_config_file",
    # File I/O
    "ensure_file",
    "safe_write",
    "safe_read",
    "write_json",
    "read_json",
    "resolve_path",
    "get_file_size",
    "file_exists",
    "dir_exists",
    "delete_file",
    "delete_dir",
    "list_files",
]
