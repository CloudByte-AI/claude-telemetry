"""All SQL queries related to sessions, prompts, responses, observations."""

from ..routers.db import q, build_tool_list


def get_sessions_list(search: str = ""):
    search_filter = ""
    params: tuple = ()
    if search.strip():
        search_filter = "AND (p.name LIKE ? OR s.session_id LIKE ?)"
        like = f"%{search.strip()}%"
        params = (like, like)
    return q(f"""
        SELECT s.session_id, s.cwd, s.created_at,
               p.name                       AS project_name,
               COALESCE(pc.prompt_count, 0) AS prompt_count,
               COALESCE(tc.tool_count,   0) AS tool_count
        FROM SESSION s
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS prompt_count
            FROM USER_PROMPT
            GROUP BY session_id
        ) pc ON pc.session_id = s.session_id
        LEFT JOIN (
            SELECT up.session_id, COUNT(t.tool_id) AS tool_count
            FROM TOOL t
            JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
            GROUP BY up.session_id
        ) tc ON tc.session_id = s.session_id
        WHERE 1=1 {search_filter}
        ORDER BY s.created_at DESC
    """, params)


def get_session(session_id: str):
    return q("""
        SELECT s.*, p.name AS project_name
        FROM SESSION s
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE s.session_id = ?
    """, (session_id,), one=True)


def get_session_prompts(session_id: str):
    return q("""
        SELECT * FROM USER_PROMPT
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,))


def get_prompt_response(prompt_id: str):
    return q("SELECT * FROM RESPONSE WHERE prompt_id = ? LIMIT 1", (prompt_id,), one=True)


def get_prompt_io_tokens(prompt_id: str):
    return q("SELECT * FROM IO_TOKENS WHERE prompt_id = ? LIMIT 1", (prompt_id,), one=True)


def get_prompt_tool_tokens_agg(prompt_id: str):
    return q("""
        SELECT
            COALESCE(SUM(tt.input_tokens),          0) AS input_tokens,
            COALESCE(SUM(tt.output_tokens),         0) AS output_tokens,
            COALESCE(SUM(tt.cache_creation_tokens), 0) AS cache_creation_tokens,
            COALESCE(SUM(tt.cache_read_tokens),     0) AS cache_read_tokens
        FROM TOOL_TOKENS tt
        JOIN TOOL t ON t.tool_id = tt.tool_id
        WHERE t.prompt_id = ?
    """, (prompt_id,), one=True)



def get_session_observations(session_id: str):
    return q(
        "SELECT * FROM HOOK_OBSERVATION WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,)
    )


def get_all_observations():
    return q("""
        SELECT o.id, o.session_id, o.title, o.subtitle, o.narrative,
               o.type, o.concepts, o.facts, o.created_at, o.sync_status
        FROM HOOK_OBSERVATION o
        JOIN SESSION s ON s.session_id = o.session_id
        ORDER BY o.created_at DESC
    """)


def get_conversation_prompt(prompt_id: str):
    return q("""
        SELECT up.*, s.session_id, s.cwd, p.name AS project_name
        FROM USER_PROMPT up
        JOIN SESSION s      ON s.session_id = up.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE up.prompt_id = ?
    """, (prompt_id,), one=True)


def get_session_prompt_ids(session_id: str):
    rows = q("""
        SELECT prompt_id FROM USER_PROMPT
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,))
    return [r["prompt_id"] for r in rows]


def get_prompt_observation(prompt_id: str):
    """Get the observation for a specific prompt, if one exists."""
    return q(
        "SELECT id, type, title, subtitle FROM HOOK_OBSERVATION WHERE prompt_id = ? LIMIT 1",
        (prompt_id,), one=True
    )


def get_session_task_queue_status(session_id: str):
    """Get task queue status for a session (pending, running, completed, failed counts)."""
    return q("""
        SELECT
            COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending,
            COUNT(CASE WHEN status = 'running' THEN 1 END) AS running,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed,
            COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed
        FROM TASK_QUEUE
        WHERE session_id = ?
    """, (session_id,), one=True)