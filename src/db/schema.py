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
        ai_title TEXT,
        custom_title TEXT,
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
    # prompt_id      : stable auto-gen UUID — URL key, never changes
    # jsonl_prompt_id: real ID from Claude Code JSONL — stored by stop() hook
    # entrypoint     : client used for this prompt (claude-vscode, claude-terminal, etc.)
    # claude_version : Claude Code version at time of prompt
    # git_branch     : active git branch at time of prompt
    # permission_mode: permission mode active for this prompt (default, auto, plan, etc.)
    # interrupt_reason: NULL = normal, 'tool_use' = user denied tool, 'request' = user hit ESC
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS USER_PROMPT (
        prompt_id TEXT PRIMARY KEY,
        session_id TEXT,
        uuid TEXT,
        parent_uuid TEXT,
        prompt TEXT,
        timestamp DATETIME,
        jsonl_prompt_id TEXT,
        entrypoint TEXT,
        claude_version TEXT,
        git_branch TEXT,
        permission_mode TEXT,
        interrupt_reason TEXT,
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

    # ---------------- SECURITY_SCAN_EVENT ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS SECURITY_SCAN_EVENT (
        event_id      TEXT PRIMARY KEY,
        session_id    TEXT,
        scan_target   TEXT NOT NULL,
        prompt_hash   TEXT,
        masked_text   TEXT,
        findings_json TEXT,
        finding_count INTEGER DEFAULT 0,
        blocked       INTEGER DEFAULT 0,
        scan_ms       INTEGER,
        scan_strategy TEXT,
        timestamp     DATETIME,
        FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
    );
    """)

    conn.commit()
    logger.info("Database tables created successfully")

def migrate_schema(conn: sqlite3.Connection) -> None:
    """
    Apply schema migrations for existing databases.
    Safe to run on any database — skips if column already exists.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(USER_PROMPT)")
    columns = [row[1] for row in cursor.fetchall()]

    if "jsonl_prompt_id" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN jsonl_prompt_id TEXT")
        logger.info("Migration: added jsonl_prompt_id column to USER_PROMPT")
    if "entrypoint" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN entrypoint TEXT")
        logger.info("Migration: added entrypoint column to USER_PROMPT")
    if "claude_version" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN claude_version TEXT")
        logger.info("Migration: added claude_version column to USER_PROMPT")
    if "git_branch" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN git_branch TEXT")
        logger.info("Migration: added git_branch column to USER_PROMPT")
    if "permission_mode" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN permission_mode TEXT")
        logger.info("Migration: added permission_mode column to USER_PROMPT")
    if "interrupt_reason" not in columns:
        cursor.execute("ALTER TABLE USER_PROMPT ADD COLUMN interrupt_reason TEXT")
        logger.info("Migration: added interrupt_reason column to USER_PROMPT")

    cursor.execute("PRAGMA table_info(SESSION)")
    session_columns = [row[1] for row in cursor.fetchall()]

    if "ai_title" not in session_columns:
        cursor.execute("ALTER TABLE SESSION ADD COLUMN ai_title TEXT")
        logger.info("Migration: added ai_title column to SESSION")
    if "custom_title" not in session_columns:
        cursor.execute("ALTER TABLE SESSION ADD COLUMN custom_title TEXT")
        logger.info("Migration: added custom_title column to SESSION")

    # SECURITY_SCAN_EVENT table (added in 0.1.29+, renamed from SECURITY_FINDING)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='SECURITY_FINDING'")
    if cursor.fetchone():
        # Rename old table to new name
        cursor.execute("ALTER TABLE SECURITY_FINDING RENAME TO SECURITY_SCAN_EVENT")
        cursor.execute("DROP INDEX IF EXISTS idx_security_session")
        cursor.execute("DROP INDEX IF EXISTS idx_security_target")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_session ON SECURITY_SCAN_EVENT(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_target ON SECURITY_SCAN_EVENT(scan_target)")
        logger.info("Migration: renamed SECURITY_FINDING to SECURITY_SCAN_EVENT")
    else:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='SECURITY_SCAN_EVENT'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS SECURITY_SCAN_EVENT (
                    event_id      TEXT PRIMARY KEY,
                    session_id    TEXT,
                    scan_target   TEXT NOT NULL,
                    prompt_hash   TEXT,
                    masked_text   TEXT,
                    findings_json TEXT,
                    finding_count INTEGER DEFAULT 0,
                    blocked       INTEGER DEFAULT 0,
                    scan_ms       INTEGER,
                    scan_strategy TEXT,
                    timestamp     DATETIME,
                    FOREIGN KEY (session_id) REFERENCES SESSION(session_id)
                )
            """)
            logger.info("Migration: created SECURITY_SCAN_EVENT table")

    # Rename event_id column alias — old rows used finding_id, add event_id if missing
    cursor.execute("PRAGMA table_info(SECURITY_SCAN_EVENT)")
    sse_cols = [row[1] for row in cursor.fetchall()]
    if sse_cols and "event_id" not in sse_cols and "finding_id" in sse_cols:
        # SQLite doesn't support RENAME COLUMN before 3.25; recreate is safest but
        # for now just alias via a view — old rows remain readable as event_id via INSERT
        logger.info("Migration: SECURITY_SCAN_EVENT has finding_id column (legacy), continuing")

    conn.commit()

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_session ON SECURITY_SCAN_EVENT(session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_target ON SECURITY_SCAN_EVENT(scan_target);")

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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompt_jsonl_id ON USER_PROMPT(jsonl_prompt_id);")

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
    migrate_schema(conn)  
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
    migrate_schema(conn)
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
    migrate_schema(conn)
    create_indexes(conn)

    conn.close()

    print(f"\nDatabase created successfully at:\n{db_path}")


if __name__ == "__main__":
    main()
 