"""
Logging Utilities for CloudByte

Provides structured logging with:
- File and console handlers
- Log rotation
- Different log levels
- Timestamp formatting
- Day-wise log files for easier debugging
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


# Log format strings
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DETAILED_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"


def setup_logging(
    log_level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    log_dir: Optional[Path] = None,
) -> None:
    """
    Set up logging configuration for CloudByte.

    Creates day-wise log files for easier debugging and testing:
    - cloudbyte-YYYY-MM-DD.log (general logs)
    - error-YYYY-MM-DD.log (error logs only)

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file: Whether to log to file
        log_to_console: Whether to log to console
        max_bytes: Maximum size of log file before rotation
        backup_count: Number of backup files to keep
        log_dir: Optional custom log directory
    """
    from src.common.paths import ensure_directories, get_logs_dir

    # Ensure log directory exists
    if log_to_file:
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
        else:
            ensure_directories()

    # Get current date for day-wise logging
    current_date = datetime.now().strftime("%Y-%m-%d")

    # Get log level
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Add file handler if requested
    if log_to_file:
        logs_dir = log_dir if log_dir else get_logs_dir()

        # Day-wise general log file
        log_file = logs_dir / f"cloudbyte-{current_date}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Day-wise error log file
        error_log_file = logs_dir / f"error-{current_date}.log"
        error_handler = RotatingFileHandler(
            error_log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)

    # Add console handler if requested
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Name of the logger (usually __name__ of the module)

    Returns:
        logging.Logger: Configured logger instance
    """
    return logging.getLogger(name)


class CloudByteLogger:
    """
    A wrapper class for CloudByte-specific logging with structured output.
    """

    def __init__(self, name: str):
        """
        Initialize the logger.

        Args:
            name: Name of the logger
        """
        self.logger = get_logger(name)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message with optional extra context."""
        self.logger.debug(message, extra=kwargs)

    def info(self, message: str, **kwargs) -> None:
        """Log info message with optional extra context."""
        self.logger.info(message, extra=kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message with optional extra context."""
        self.logger.warning(message, extra=kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs) -> None:
        """Log error message with optional exception info."""
        self.logger.error(message, exc_info=exc_info, extra=kwargs)

    def critical(self, message: str, exc_info: bool = False, **kwargs) -> None:
        """Log critical message with optional exception info."""
        self.logger.critical(message, exc_info=exc_info, extra=kwargs)

    def exception(self, message: str, **kwargs) -> None:
        """Log exception with full traceback."""
        self.logger.exception(message, extra=kwargs)


def get_cloudbyte_logger(name: str) -> CloudByteLogger:
    """
    Get a CloudByte logger instance.

    Args:
        name: Name of the logger

    Returns:
        CloudByteLogger: CloudByte logger instance
    """
    return CloudByteLogger(name)
