"""All SQL queries related to tool calls and tool tokens."""

from ..routers.db import q, client_where


def get_all_tools_stats(client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT t.tool_name,
               COUNT(*)                          AS call_count,
               SUM(tt.input_tokens)               AS input_tokens,
               SUM(tt.output_tokens)              AS output_tokens,
               SUM(tt.cache_read_tokens)          AS cache_read_tokens,
               SUM(tt.cache_creation_tokens)      AS cache_creation_tokens
        FROM TOOL t
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        LEFT JOIN TOOL_TOKENS tt ON tt.tool_id = t.tool_id
        WHERE 1=1 {where}
        GROUP BY t.tool_name
        ORDER BY call_count DESC
    """, params)


def get_tools_stats_by_date(d_from: str, d_to: str, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT t.tool_name,
               COUNT(*)                     AS call_count,
               SUM(tt.input_tokens)          AS input_tokens,
               SUM(tt.output_tokens)         AS output_tokens,
               SUM(tt.cache_read_tokens)     AS cache_read_tokens,
               SUM(tt.cache_creation_tokens) AS cache_creation_tokens
        FROM TOOL t
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        LEFT JOIN TOOL_TOKENS tt ON tt.tool_id = t.tool_id
        WHERE substr(up.timestamp, 1, 10) BETWEEN ? AND ? {where}
        GROUP BY t.tool_name
        ORDER BY call_count DESC
    """, (d_from, d_to) + params)


def get_tools_overall_summary(client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT COUNT(*)                       AS total_calls,
               COUNT(DISTINCT tool_name)      AS unique_tools,
               COUNT(DISTINCT up.session_id)  AS sessions_with_tools,
               COUNT(DISTINCT s.project_id)   AS projects_with_tools
        FROM TOOL t
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        WHERE 1=1 {where}
    """, params, one=True)


def get_sessions_tool_breakdown(search: str = "", client: str = None):
    search_filter = ""
    search_params: tuple = ()
    if search.strip():
        search_filter = "AND (p.name LIKE ? OR s.session_id LIKE ?)"
        like = f"%{search.strip()}%"
        search_params = (like, like)
    where, where_params = client_where(client, "s")
    return q(f"""
        SELECT s.session_id, p.name AS project_name,
               substr(s.created_at, 1, 10)  AS session_date,
               COALESCE(tc.tool_calls,   0) AS tool_calls,
               COALESCE(tc.unique_tools, 0) AS unique_tools,
               tk.input_tokens          AS input_tokens,
               tk.output_tokens         AS output_tokens,
               tk.cache_read_tokens     AS cache_read_tokens,
               tk.cache_creation_tokens AS cache_creation_tokens
        FROM SESSION s
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        LEFT JOIN (
            SELECT up.session_id,
                   COUNT(t.tool_id)            AS tool_calls,
                   COUNT(DISTINCT t.tool_name) AS unique_tools
            FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
            GROUP BY up.session_id
        ) tc ON tc.session_id = s.session_id
        LEFT JOIN (
            SELECT up.session_id,
                   COALESCE(SUM(tt.input_tokens),          0) AS input_tokens,
                   COALESCE(SUM(tt.output_tokens),         0) AS output_tokens,
                   COALESCE(SUM(tt.cache_read_tokens),     0) AS cache_read_tokens,
                   COALESCE(SUM(tt.cache_creation_tokens), 0) AS cache_creation_tokens
            FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
            JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
            GROUP BY up.session_id
        ) tk ON tk.session_id = s.session_id
        WHERE tc.tool_calls > 0 {search_filter} {where}
        ORDER BY tc.tool_calls DESC
    """, search_params + where_params)


def get_projects_tool_breakdown(search: str = "", client: str = None):
    search_filter = ""
    search_params: tuple = ()
    if search.strip():
        search_filter = "AND p.name LIKE ?"
        search_params = (f"%{search.strip()}%",)
    where, where_params = client_where(client, "s")
    return q(f"""
        SELECT p.project_id, p.name AS project_name,
               COALESCE(sc.session_count, 0) AS session_count,
               COALESCE(tc.tool_calls,    0) AS tool_calls,
               COALESCE(tc.unique_tools,  0) AS unique_tools,
               tk.input_tokens          AS input_tokens,
               tk.output_tokens         AS output_tokens,
               tk.cache_read_tokens     AS cache_read_tokens,
               tk.cache_creation_tokens AS cache_creation_tokens
        FROM PROJECT p
        LEFT JOIN (SELECT s.project_id, COUNT(*) AS session_count FROM SESSION s WHERE 1=1 {where} GROUP BY s.project_id) sc ON sc.project_id = p.project_id
        LEFT JOIN (
            SELECT s.project_id,
                   COUNT(t.tool_id)            AS tool_calls,
                   COUNT(DISTINCT t.tool_name) AS unique_tools
            FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
            JOIN SESSION s ON s.session_id=up.session_id
            WHERE 1=1 {where}
            GROUP BY s.project_id
        ) tc ON tc.project_id = p.project_id
        LEFT JOIN (
            SELECT s.project_id,
                   COALESCE(SUM(tt.input_tokens),          0) AS input_tokens,
                   COALESCE(SUM(tt.output_tokens),         0) AS output_tokens,
                   COALESCE(SUM(tt.cache_read_tokens),     0) AS cache_read_tokens,
                   COALESCE(SUM(tt.cache_creation_tokens), 0) AS cache_creation_tokens
            FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
            JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
            JOIN SESSION s ON s.session_id=up.session_id
            WHERE 1=1 {where}
            GROUP BY s.project_id
        ) tk ON tk.project_id = p.project_id
        WHERE tc.tool_calls > 0 {search_filter}
        ORDER BY tc.tool_calls DESC
    """, where_params * 3 + search_params)


def get_session_tool_stats(session_id: str):
    return q("""
        SELECT COUNT(DISTINCT t.prompt_id) AS turns_with_tools,
               COUNT(DISTINCT t.tool_name) AS unique_tools
        FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        WHERE up.session_id = ?
    """, (session_id,), one=True)


def get_session_tools_breakdown(session_id: str):
    return q("""
        SELECT t.tool_name,
               COUNT(*)                     AS call_count,
               SUM(tt.input_tokens)          AS input_tokens,
               SUM(tt.output_tokens)         AS output_tokens,
               SUM(tt.cache_read_tokens)     AS cache_read_tokens,
               SUM(tt.cache_creation_tokens) AS cache_creation_tokens,
               SUM(CASE WHEN t.output_json LIKE '%"error"%' THEN 1 ELSE 0 END) AS errors
        FROM TOOL t
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        LEFT JOIN TOOL_TOKENS tt ON tt.tool_id = t.tool_id
        WHERE up.session_id = ?
        GROUP BY t.tool_name
        ORDER BY call_count DESC
    """, (session_id,))


def get_session_turns_tool_breakdown(session_id: str):
    return q("""
        SELECT up.prompt_id, up.prompt,
               substr(up.timestamp, 1, 16) AS ts,
               COALESCE(tc.tool_count,   0) AS tool_count,
               COALESCE(tc.unique_tools, 0) AS unique_tools,
               tk.turn_tokens AS turn_tokens
        FROM USER_PROMPT up
        LEFT JOIN (
            SELECT prompt_id, COUNT(*) AS tool_count, COUNT(DISTINCT tool_name) AS unique_tools
            FROM TOOL GROUP BY prompt_id
        ) tc ON tc.prompt_id = up.prompt_id
        LEFT JOIN (
            SELECT t.prompt_id,
                   COALESCE(SUM(tt.input_tokens+tt.output_tokens+
                                tt.cache_read_tokens+tt.cache_creation_tokens),0) AS turn_tokens
            FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
            GROUP BY t.prompt_id
        ) tk ON tk.prompt_id = up.prompt_id
        WHERE up.session_id = ? AND tc.tool_count > 0
        ORDER BY up.timestamp ASC
    """, (session_id,))


def get_project_tool_stats(project_id: str):
    return q("""
        SELECT COUNT(DISTINCT t.prompt_id) AS turns_with_tools,
               COUNT(DISTINCT t.tool_name) AS unique_tools
        FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s ON s.session_id = up.session_id
        WHERE s.project_id = ?
    """, (project_id,), one=True)


def get_project_tools_breakdown(project_id: str):
    return q("""
        SELECT t.tool_name,
               COUNT(*)                     AS call_count,
               COUNT(DISTINCT up.session_id) AS sessions_used_in,
               SUM(tt.input_tokens)          AS input_tokens,
               SUM(tt.output_tokens)         AS output_tokens,
               SUM(tt.cache_read_tokens)     AS cache_read_tokens,
               SUM(tt.cache_creation_tokens) AS cache_creation_tokens,
               SUM(CASE WHEN t.output_json LIKE '%"error"%' THEN 1 ELSE 0 END) AS errors
        FROM TOOL t
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        LEFT JOIN TOOL_TOKENS tt ON tt.tool_id = t.tool_id
        WHERE s.project_id = ?
        GROUP BY t.tool_name
        ORDER BY call_count DESC
    """, (project_id,))


def get_project_sessions_tool_breakdown(project_id: str):
    return q("""
        SELECT s.session_id, substr(s.created_at, 1, 10) AS session_date,
               COALESCE(tc.tool_calls,    0) AS tool_calls,
               COALESCE(tc.unique_tools,  0) AS unique_tools,
               tk.input_tokens          AS input_tokens,
               tk.output_tokens         AS output_tokens,
               tk.cache_read_tokens     AS cache_read_tokens,
               tk.cache_creation_tokens AS cache_creation_tokens
        FROM SESSION s
        LEFT JOIN (
            SELECT up.session_id, COUNT(t.tool_id) AS tool_calls, COUNT(DISTINCT t.tool_name) AS unique_tools
            FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id GROUP BY up.session_id
        ) tc ON tc.session_id = s.session_id
        LEFT JOIN (
            SELECT up.session_id,
                   COALESCE(SUM(tt.input_tokens),0) AS input_tokens,
                   COALESCE(SUM(tt.output_tokens),0) AS output_tokens,
                   COALESCE(SUM(tt.cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(tt.cache_creation_tokens),0) AS cache_creation_tokens
            FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
            JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id GROUP BY up.session_id
        ) tk ON tk.session_id = s.session_id
        WHERE s.project_id = ? AND tc.tool_calls > 0
        ORDER BY tc.tool_calls DESC
    """, (project_id,))


def get_session_tool_token_totals(session_id: str):
    return q("""
        SELECT SUM(tt.input_tokens)          AS input_tokens,
               SUM(tt.output_tokens)         AS output_tokens,
               SUM(tt.cache_read_tokens)     AS cache_read_tokens,
               SUM(tt.cache_creation_tokens) AS cache_creation_tokens
        FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
        JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
        WHERE up.session_id=?
    """, (session_id,), one=True)


def get_project_tool_token_totals(project_id: str):
    return q("""
        SELECT SUM(tt.input_tokens)          AS input_tokens,
               SUM(tt.output_tokens)         AS output_tokens,
               SUM(tt.cache_read_tokens)     AS cache_read_tokens,
               SUM(tt.cache_creation_tokens) AS cache_creation_tokens
        FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
        JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
        JOIN SESSION s ON s.session_id=up.session_id
        WHERE s.project_id=?
    """, (project_id,), one=True)


def get_session_tool_counts(session_id: str):
    """Total calls, unique tools, active turns for a session."""
    return q("""
        SELECT COUNT(t.tool_id)            AS total_calls,
               COUNT(DISTINCT t.tool_name) AS unique_tools,
               COUNT(DISTINCT t.prompt_id) AS active_turns
        FROM TOOL t JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
        WHERE up.session_id=?
    """, (session_id,), one=True)


def get_session_prompt_order(session_id: str):
    """Return {prompt_id: turn_number} mapping for a session."""
    rows = q(
        "SELECT prompt_id FROM USER_PROMPT WHERE session_id=? ORDER BY timestamp ASC",
        (session_id,)
    )
    return {r["prompt_id"]: i + 1 for i, r in enumerate(rows)}