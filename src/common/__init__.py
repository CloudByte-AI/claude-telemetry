"""
Common utilities for CloudByte.

This module contains shared infrastructure code including logging,
path management, file I/O, and configuration.

Imports are LAZY (PEP 562), so importing one submodule, such as
src.common.paths, does not load logging and the rest on every hook call. The
package-level re-exports still work; each submodule loads on first use.
"""

_EXPORTS = {
    # Logging
    "get_logger": "src.common.logging",
    "setup_logging": "src.common.logging",
    "CloudByteLogger": "src.common.logging",
    "get_cloudbyte_logger": "src.common.logging",
    # Paths
    "get_home_dir": "src.common.paths",
    "get_cloudbyte_dir": "src.common.paths",
    "get_data_dir": "src.common.paths",
    "get_logs_dir": "src.common.paths",
    "get_db_path": "src.common.paths",
    "get_log_file": "src.common.paths",
    "ensure_directories": "src.common.paths",
    "get_config_file": "src.common.paths",
    # File I/O
    "ensure_file": "src.common.file_io",
    "safe_write": "src.common.file_io",
    "safe_read": "src.common.file_io",
    "write_json": "src.common.file_io",
    "read_json": "src.common.file_io",
    "resolve_path": "src.common.file_io",
    "get_file_size": "src.common.file_io",
    "file_exists": "src.common.file_io",
    "dir_exists": "src.common.file_io",
    "delete_file": "src.common.file_io",
    "delete_dir": "src.common.file_io",
    "list_files": "src.common.file_io",
    # Time
    "get_now_ist": "src.common.time_utils",
    "get_now_ist_iso": "src.common.time_utils",
    "to_ist": "src.common.time_utils",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    """PEP 562 lazy attribute access - the submodule is imported on first use."""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    return getattr(importlib.import_module(module_path), name)


def __dir__():
    return sorted(__all__)
