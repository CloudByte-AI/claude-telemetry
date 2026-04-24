"""
Database Management Utilities for CloudByte

Provides:
- Connection management
- Context managers for safe operations
- Connection pooling/reuse
- Error handling and recovery
"""

import sqlite3
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from src.common.logging import get_logger
from src.common.paths import get_db_path, ensure_directories


logger = get_logger(__name__)


def retry_on_lock(retries: int = 3, delay: float = 0.5) -> Callable:
    """
    Decorator to retry database operations on "database is locked" errors.

    Args:
        retries: Number of retry attempts
        delay: Initial delay between retries in seconds (exponential backoff)

    Returns:
        Decorated function that retries on lock errors
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < retries - 1:
                        wait_time = delay * (2 ** attempt)
                        logger.debug(f"Database locked, retrying in {wait_time}s (attempt {attempt + 1}/{retries})")
                        time.sleep(wait_time)
                        continue
                    raise
            return None
        return wrapper
    return decorator


class DatabaseManager:
    """
    Manages database connections and operations for CloudByte.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the database manager.

        Args:
            db_path: Path to the database file. Defaults to get_db_path()
        """
        self.db_path = db_path or get_db_path()
        self._connection: Optional[sqlite3.Connection] = None
        self._schema_checked = False

    def get_connection(self) -> sqlite3.Connection:
        """
        Get a database connection, creating one if needed.

        Returns:
            sqlite3.Connection: Database connection with foreign keys enabled
        """
        if self._connection is None:
            self._connection = self._create_connection()
            # Ensure schema is initialized on first connection
            self.ensure_schema_initialized()
        return self._connection

    def _create_connection(self) -> sqlite3.Connection:
        """
        Create a new database connection.

        Returns:
            sqlite3.Connection: New database connection
        """
        # Ensure data directory exists
        ensure_directories()

        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # Allow sharing across threads
            timeout=60.0,  # Increase timeout to 60 seconds
        )
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")  # Wait up to 60 seconds for locks

        logger.debug(f"Created database connection to {self.db_path}")
        return conn

    def ensure_schema_initialized(self) -> None:
        """
        Ensure database tables are initialized.
        Creates tables if they don't exist.

        Note: This method must be called explicitly after initialization
        to avoid circular import issues.
        """
        if self._schema_checked:
            return

        try:
            from src.db.schema import DatabaseSchema

            # Check if schema is already initialized
            if DatabaseSchema.verify_schema(self._connection):
                logger.debug("Database schema already initialized")
                self._schema_checked = True
                return

            # Initialize schema
            logger.info("Database schema not found, initializing...")
            from src.db.schema import create_tables, create_indexes

            create_tables(self._connection)
            create_indexes(self._connection)
            logger.info("Database schema initialized successfully")
            self._schema_checked = True

        except Exception as e:
            logger.error(f"Error initializing database schema: {e}")
            # Don't raise - allow connection to proceed even if schema init fails
            # The setup() hook will handle full initialization

    def close(self) -> None:
        """Close the database connection if open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            logger.debug("Closed database connection")

    def is_connected(self) -> bool:
        """
        Check if a connection is currently open.

        Returns:
            bool: True if connected, False otherwise
        """
        return self._connection is not None

    @contextmanager
    def transaction(self):
        """
        Context manager for database transactions.
        Automatically commits on success, rolls back on error.

        Yields:
            sqlite3.Connection: Database connection

        Example:
            with db_manager.transaction():
                cursor.execute("INSERT INTO ...")
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            yield cursor
            conn.commit()
            logger.debug("Transaction committed successfully")
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction failed, rolled back: {e}")
            raise

    @contextmanager
    def cursor(self):
        """
        Context manager for database cursor operations.

        Yields:
            sqlite3.Cursor: Database cursor
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            yield cursor
        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a SQL statement.

        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement

        Returns:
            sqlite3.Cursor: Cursor with the result
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """
        Execute a SQL statement multiple times with different parameters.

        Args:
            sql: SQL statement to execute
            params_list: List of parameter tuples

        Returns:
            sqlite3.Cursor: Cursor with the result
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executemany(sql, params_list)
        return cursor

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[tuple]:
        """
        Execute SQL and fetch one row.

        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement

        Returns:
            Optional[tuple]: First row or None if no results
        """
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list:
        """
        Execute SQL and fetch all rows.

        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement

        Returns:
            list: All result rows
        """
        cursor = self.execute(sql, params)
        return cursor.fetchall()


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """
    Get the global database manager instance.

    Returns:
        DatabaseManager: Global database manager
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_db_connection() -> sqlite3.Connection:
    """
    Get a database connection from the global manager.

    Returns:
        sqlite3.Connection: Database connection
    """
    return get_db_manager().get_connection()


@contextmanager
def db_transaction():
    """
    Context manager for database transactions using global manager.

    Yields:
        sqlite3.Cursor: Database cursor

    Example:
        with db_transaction() as cursor:
            cursor.execute("INSERT INTO ...")
    """
    with get_db_manager().transaction() as cursor:
        yield cursor


@contextmanager
def db_cursor():
    """
    Context manager for database cursor using global manager.

    Yields:
        sqlite3.Cursor: Database cursor
    """
    with get_db_manager().cursor() as cursor:
        yield cursor


def close_db() -> None:
    """Close the global database connection."""
    global _db_manager
    if _db_manager is not None:
        _db_manager.close()
