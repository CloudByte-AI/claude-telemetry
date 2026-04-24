"""
CloudByte Database Module

Handles database schema creation, management, and data writing.
"""

from .schema import (
    create_tables,
    create_indexes,
    initialize_database,
    initialize_database_with_manager,
    DatabaseSchema,
)
from .manager import (
    DatabaseManager,
    get_db_manager,
    get_db_connection,
    db_transaction,
    db_cursor,
    close_db,
)
from .writers import (
    DatabaseWriter,
    write_project,
    write_session,
    write_user_prompt,
)

__all__ = [
    # Schema
    "create_tables",
    "create_indexes",
    "initialize_database",
    "initialize_database_with_manager",
    "DatabaseSchema",
    # Manager
    "DatabaseManager",
    "get_db_manager",
    "get_db_connection",
    "db_transaction",
    "db_cursor",
    "close_db",
    # Writers
    "DatabaseWriter",
    "write_project",
    "write_session",
    "write_user_prompt",
]
