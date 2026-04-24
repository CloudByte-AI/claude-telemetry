"""
Database Schema for CloudByte

Defines all tables and indexes for the CloudByte analytics system.
"""

import sqlite3
from datetime import datetime
from typing import Optional

from src.common.logging import get_logger
from src.db.manager import DatabaseManager
from src.common.paths import get_db_path, ensure_directories


logger = get_logger(__name__)


def get_db_path_from_user() -> str:
    """
    Prompt user for database path (for manual setup).
    Note: This is mainly for the original standalone script.
    For the plugin, we use the default path.

    Returns:
        str: Path to the database file
    """
    path = input("Enter full path where SQLite DB should be created (e.g., D:/data/claude.db): ").strip()

    if not path.endswith(".db"):
        path += ".db"

    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def create_connection(db_path: str) -> sqlite3.Connection:
    """
    Create a database connection.

    Args:
        db_path: Path to the database file

    Returns:
        sqlite3.Connection: Database connection with foreign keys enabled
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """
    Create all database tables.

    Args:
        conn: Database connection
    """
    cursor = conn.cursor()

    # ---------------- PROJECT ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS PROJECT (
        project_id TEXT PRIMARY KEY,
        name TEXT,
        path TEXT,
        created_at DATETIME
    );
    """)

    # ---------------- SESSION ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS SESSION (
        session_id TEXT PRIMARY KEY,
        project_id TEXT,
        cwd TEXT,
        jsonl_file TEXT,
        created_at DATETIME,
        FOREIGN KEY (project_id) REFERENCES PROJECT(project_id)
    );
    """)

    # ---------------- RAW_LOG ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS RAW_LOG (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        type TEXT,
        raw_json TEXT,
        timestamp DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
    );
    """)

    # ---------------- USER_PROMPT ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS USER_PROMPT (
        prompt_id TEXT PRIMARY KEY,
        session_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        prompt TEXT,
        timestamp DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
    );
    """)

    # ---------------- RESPONSE ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS RESPONSE (
        message_id TEXT PRIMARY KEY,
        prompt_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        response_text TEXT,
        model TEXT,
        timestamp DATETIME,
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id)
    );
    """)

    # ---------------- THINKING ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS THINKING (
        thinking_id TEXT PRIMARY KEY,
        prompt_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        content TEXT,
        signature TEXT,
        timestamp DATETIME,
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id)
    );
    """)

    # ---------------- TOOL ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS TOOL (
        tool_id TEXT PRIMARY KEY,
        prompt_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        tool_name TEXT,
        model TEXT,
        input_json TEXT,
        output_json TEXT,
        timestamp DATETIME,
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id)
    );
    """)

    # ---------------- IO_TOKENS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS IO_TOKENS (
        id TEXT PRIMARY KEY,
        prompt_id TEXT,
        message_id TEXT,
        token_type TEXT,
        input_tokens INTEGER,
        cache_creation_tokens INTEGER,
        cache_read_tokens INTEGER,
        output_tokens INTEGER,
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id),
        FOREIGN KEY (message_id) REFERENCES RESPONSE(message_id)
    );
    """)

    # ---------------- TOOL_TOKENS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS TOOL_TOKENS (
        id TEXT PRIMARY KEY,
        tool_id TEXT,
        input_tokens INTEGER,
        cache_creation_tokens INTEGER,
        cache_read_tokens INTEGER,
        output_tokens INTEGER,
        FOREIGN KEY (tool_id) REFERENCES TOOL(tool_id)
    );
    """)

    # ---------------- OBSERVATION ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS OBSERVATION (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        prompt_id TEXT,
        title TEXT,
        subtitle TEXT,
        narrative TEXT,
        text TEXT,
        facts TEXT,
        concepts TEXT,
        type TEXT,
        files_read TEXT,
        files_modified TEXT,
        content_hash TEXT,
        created_at DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id),
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id)
    );
    """)

    # ---------------- HOOK_OBSERVATION ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS HOOK_OBSERVATION (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        prompt_id TEXT,
        title TEXT,
        subtitle TEXT,
        narrative TEXT,
        text TEXT,
        facts TEXT,
        concepts TEXT,
        type TEXT,
        files_read TEXT,
        files_modified TEXT,
        content_hash TEXT,
        created_at DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id),
        FOREIGN KEY (prompt_id) REFERENCES USER_PROMPT(prompt_id)
    );
    """)

    # ---------------- SESSION_SUMMARY ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS SESSION_SUMMARY (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        project TEXT,
        request TEXT,
        investigated TEXT,
        learned TEXT,
        completed TEXT,
        next_steps TEXT,
        notes TEXT,
        created_at DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
    );
    """)

    # ---------------- TASK_QUEUE ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS TASK_QUEUE (
        id TEXT PRIMARY KEY,
        task_type TEXT NOT NULL,
        session_id TEXT NOT NULL,
        prompt_id TEXT,
        status TEXT NOT NULL,
        priority INTEGER DEFAULT 0,
        payload TEXT,
        error_message TEXT,
        created_at DATETIME,
        started_at DATETIME,
        completed_at DATETIME,
        retry_count INTEGER DEFAULT 0,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
    );
    """)

    conn.commit()
    logger.info("Database tables created successfully")


def create_indexes(conn: sqlite3.Connection) -> None:
    """
    Create database indexes for performance.

    Args:
        conn: Database connection
    """
    cursor = conn.cursor()

    # Indexes for foreign key relationships
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_project ON SESSION(project_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompt_session ON USER_PROMPT(session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_response_prompt ON RESPONSE(prompt_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tool_prompt ON TOOL(prompt_id);")

    # Indexes for HOOK_OBSERVATION
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hook_obs_session ON HOOK_OBSERVATION(session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hook_obs_prompt ON HOOK_OBSERVATION(prompt_id);")

    # Indexes for UUID lookups
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_uuid_raw ON RAW_LOG(uuid);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent_uuid_raw ON RAW_LOG(parent_uuid);")

    # Indexes for token tables
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_io_prompt ON IO_TOKENS(prompt_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_io_message ON IO_TOKENS(message_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tool_tokens_tool ON TOOL_TOKENS(tool_id);")

    # Indexes for task queue
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_queue_status ON TASK_QUEUE(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_queue_priority ON TASK_QUEUE(priority DESC, created_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_queue_session ON TASK_QUEUE(session_id);")

    conn.commit()
    logger.info("Database indexes created successfully")


def initialize_database(db_path: Optional[str] = None) -> None:
    """
    Initialize the database with tables and indexes.

    Args:
        db_path: Optional custom database path. Uses default if not provided.
    """
    if db_path is None:
        db_path = str(get_db_path())
    else:
        # Ensure custom path directory exists
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = create_connection(db_path)
    create_tables(conn)
    create_indexes(conn)
    conn.close()

    logger.info(f"Database initialized at: {db_path}")


def initialize_database_with_manager(db_manager: DatabaseManager) -> None:
    """
    Initialize database using the DatabaseManager.

    Args:
        db_manager: DatabaseManager instance
    """
    ensure_directories()
    conn = db_manager.get_connection()
    create_tables(conn)
    create_indexes(conn)
    logger.info(f"Database initialized at: {db_manager.db_path}")


class DatabaseSchema:
    """
    Helper class for database schema operations.
    """

    # Table names
    TABLE_PROJECT = "PROJECT"
    TABLE_SESSION = "SESSION"
    TABLE_RAW_LOG = "RAW_LOG"
    TABLE_USER_PROMPT = "USER_PROMPT"
    TABLE_RESPONSE = "RESPONSE"
    TABLE_THINKING = "THINKING"
    TABLE_TOOL = "TOOL"
    TABLE_IO_TOKENS = "IO_TOKENS"
    TABLE_TOOL_TOKENS = "TOOL_TOKENS"
    TABLE_OBSERVATION = "OBSERVATION"
    TABLE_SESSION_SUMMARY = "SESSION_SUMMARY"
    TABLE_TASK_QUEUE = "TASK_QUEUE"

    # All tables
    ALL_TABLES = [
        TABLE_PROJECT,
        TABLE_SESSION,
        TABLE_RAW_LOG,
        TABLE_USER_PROMPT,
        TABLE_RESPONSE,
        TABLE_THINKING,
        TABLE_TOOL,
        TABLE_IO_TOKENS,
        TABLE_TOOL_TOKENS,
        TABLE_OBSERVATION,
        TABLE_SESSION_SUMMARY,
        TABLE_TASK_QUEUE,
    ]

    @classmethod
    def table_exists(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            conn: Database connection
            table_name: Name of the table to check

        Returns:
            bool: True if table exists
        """
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name=?
        """, (table_name,))
        return cursor.fetchone() is not None

    @classmethod
    def get_all_tables(cls, conn: sqlite3.Connection) -> list[str]:
        """
        Get all table names in the database.

        Args:
            conn: Database connection

        Returns:
            list[str]: List of table names
        """
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name
        """)
        return [row[0] for row in cursor.fetchall()]

    @classmethod
    def verify_schema(cls, conn: sqlite3.Connection) -> bool:
        """
        Verify that all required tables exist.

        Args:
            conn: Database connection

        Returns:
            bool: True if all tables exist
        """
        for table in cls.ALL_TABLES:
            if not cls.table_exists(conn, table):
                logger.warning(f"Missing table: {table}")
                return False
        return True


def main():
    """Main function for standalone database initialization."""
    print("=== Claude JSONL Analytics DB Creator ===")

    db_path = get_db_path_from_user()
    conn = create_connection(db_path)

    create_tables(conn)
    create_indexes(conn)

    conn.close()

    print(f"\nDatabase created successfully at:\n{db_path}")


if __name__ == "__main__":
    main()
