"""
Path Management Utilities for CloudByte

Handles all path-related operations including:
- CloudByte data directory in user home
- Database path
- Log file paths
- Automatic directory creation
"""

import os
from pathlib import Path
from typing import Optional

# CloudByte directory name
CLOUDBYTE_DIR = ".cloudbyte"
DATA_SUBDIR = "data"
LOGS_SUBDIR = "logs"
DB_FILENAME = "cloudbyte.db"


def get_home_dir() -> Path:
    """
    Get the user's home directory.

    Returns:
        Path: The user's home directory path
    """
    return Path.home()


def get_cloudbyte_dir() -> Path:
    """
    Get the CloudByte directory path in user's home folder.
    Typically: C:\\Users\\<username>\\.cloudbyte

    Returns:
        Path: The CloudByte directory path
    """
    return get_home_dir() / CLOUDBYTE_DIR


def get_data_dir() -> Path:
    """
    Get the data directory path for storing databases.
    Typically: C:\\Users\\<username>\\.cloudbyte\\data

    Returns:
        Path: The data directory path
    """
    return get_cloudbyte_dir() / DATA_SUBDIR


def get_logs_dir() -> Path:
    """
    Get the logs directory path for storing log files.
    Typically: C:\\Users\\<username>\\.cloudbyte\\logs

    Returns:
        Path: The logs directory path
    """
    return get_cloudbyte_dir() / LOGS_SUBDIR


def get_db_path(db_name: Optional[str] = None) -> Path:
    """
    Get the database file path.

    Args:
        db_name: Optional database name. Defaults to 'cloudbyte.db'

    Returns:
        Path: The full path to the database file
    """
    if db_name is None:
        db_name = DB_FILENAME

    if not db_name.endswith(".db"):
        db_name += ".db"

    return get_data_dir() / db_name


def get_log_file(log_name: str = "cloudbyte.log") -> Path:
    """
    Get a log file path.

    Args:
        log_name: Name of the log file

    Returns:
        Path: The full path to the log file
    """
    return get_logs_dir() / log_name


def ensure_directories() -> None:
    """
    Ensure all required CloudByte directories exist.
    Creates:
    - .cloudbyte/
    - .cloudbyte/data/
    - .cloudbyte/logs/
    """
    directories = [
        get_cloudbyte_dir(),
        get_data_dir(),
        get_logs_dir(),
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def get_config_file() -> Path:
    """
    Get the configuration file path.

    Returns:
        Path: The full path to config.json
    """
    return get_cloudbyte_dir() / "config.json"
