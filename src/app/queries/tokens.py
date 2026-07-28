"""All SQL queries related to IO tokens and tool tokens."""

from ..routers.db import q, client_where


def get_io_totals_by_date(d_from: str, d_to: str, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT COALESCE(SUM(i.input_tokens),0) AS inp,
               COALESCE(SUM(i.output_tokens),0) AS out,
               COALESCE(SUM(i.cache_read_tokens),0) AS cr,
               COALESCE(SUM(i.cache_creation_tokens),0) AS cc
        FROM IO_TOKENS i
        JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        WHERE substr(s.created_at, 1, 10) BETWEEN ? AND ? {where}
    """, (d_from, d_to) + params, one=True)


def get_tool_totals_by_date(d_from: str, d_to: str, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT SUM(tt.input_tokens) AS inp,
               SUM(tt.output_tokens) AS out,
               SUM(tt.cache_read_tokens) AS cr,
               SUM(tt.cache_creation_tokens) AS cc
        FROM TOOL_TOKENS tt
        JOIN TOOL t         ON t.tool_id    = tt.tool_id
        JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        WHERE substr(s.created_at, 1, 10) BETWEEN ? AND ? {where}
    """, (d_from, d_to) + params, one=True)


def get_chart_sessions_by_date(d_from: str, d_to: str, limit: int = 10, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT s.session_id,
               COALESCE(SUM(i.input_tokens+i.output_tokens+
                            i.cache_read_tokens+i.cache_creation_tokens),0) AS io_total
        FROM IO_TOKENS i
        JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        WHERE substr(s.created_at, 1, 10) BETWEEN ? AND ? {where}
        GROUP BY s.session_id
        ORDER BY io_total DESC
        LIMIT ?
    """, (d_from, d_to) + params + (limit,))


def get_session_tool_total(session_id: str):
    return q("""
        SELECT SUM(tt.input_tokens+tt.output_tokens+
                   tt.cache_read_tokens+tt.cache_creation_tokens) AS total
        FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
        WHERE t.prompt_id IN (SELECT prompt_id FROM USER_PROMPT WHERE session_id=?)
    """, (session_id,), one=True)


def get_sessions_io_breakdown(search: str = "", client: str = None):
    search_filter = ""
    search_params: tuple = ()
    if search.strip():
        search_filter = "AND (p.name LIKE ? OR s.session_id LIKE ?)"
        like = f"%{search.strip()}%"
        search_params = (like, like)
    where, where_params = client_where(client, "s")
    return q(f"""
        SELECT s.session_id, p.name AS project_name, s.cwd,
               substr(s.created_at, 1, 10) AS session_date,
               COUNT(DISTINCT i.prompt_id)  AS turn_count,
               COALESCE(SUM(i.input_tokens),0)          AS io_input,
               COALESCE(SUM(i.output_tokens),0)         AS io_output,
               COALESCE(SUM(i.cache_read_tokens),0)     AS io_cache_read,
               COALESCE(SUM(i.cache_creation_tokens),0) AS io_cache_create
        FROM IO_TOKENS i
        JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
        JOIN SESSION s      ON s.session_id = up.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE 1=1 {search_filter} {where}
        GROUP BY s.session_id
        ORDER BY SUM(i.input_tokens+i.output_tokens+i.cache_read_tokens) DESC
    """, search_params + where_params)


def get_projects_io_breakdown(search: str = "", client: str = None):
    search_filter = ""
    search_params: tuple = ()
    if search.strip():
        search_filter = "AND p.name LIKE ?"
        search_params = (f"%{search.strip()}%",)
    where, where_params = client_where(client, "s")
    return q(f"""
        SELECT p.project_id, p.name AS project_name, p.path,
               COALESCE(sc.session_count, 0) AS session_count,
               COALESCE(io.inp, 0) AS io_input,
               COALESCE(io.out, 0) AS io_output,
               COALESCE(io.cr,  0) AS io_cache_read,
               COALESCE(io.cc,  0) AS io_cache_create
        FROM PROJECT p
        LEFT JOIN (
            SELECT s.project_id, COUNT(*) AS session_count FROM SESSION s WHERE 1=1 {where} GROUP BY s.project_id
        ) sc ON sc.project_id = p.project_id
        LEFT JOIN (
            SELECT s.project_id,
                   SUM(i.input_tokens) AS inp, SUM(i.output_tokens) AS out,
                   SUM(i.cache_read_tokens) AS cr, SUM(i.cache_creation_tokens) AS cc
            FROM IO_TOKENS i
            JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
            JOIN SESSION s      ON s.session_id = up.session_id
            WHERE 1=1 {where}
            GROUP BY s.project_id
        ) io ON io.project_id = p.project_id
        WHERE io.inp IS NOT NULL {search_filter}
        ORDER BY io.inp DESC
    """, where_params * 2 + search_params)


def get_session_io_totals(session_id: str):
    return q("""
        SELECT COALESCE(SUM(i.input_tokens),0) AS inp,
               COALESCE(SUM(i.output_tokens),0) AS out,
               COALESCE(SUM(i.cache_read_tokens),0) AS cr,
               COALESCE(SUM(i.cache_creation_tokens),0) AS cc
        FROM IO_TOKENS i JOIN USER_PROMPT up ON up.prompt_id=i.prompt_id
        WHERE up.session_id=?
    """, (session_id,), one=True)


def get_session_tool_totals(session_id: str):
    return q("""
        SELECT SUM(tt.input_tokens) AS inp,
               SUM(tt.output_tokens) AS out,
               SUM(tt.cache_read_tokens) AS cr,
               SUM(tt.cache_creation_tokens) AS cc
        FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
        JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
        WHERE up.session_id=?
    """, (session_id,), one=True)


def get_session_turn_io_breakdown(session_id: str):
    return q("""
        SELECT up.prompt_id, up.prompt,
               substr(up.timestamp, 1, 16) AS ts,
               i.input_tokens, i.output_tokens, i.cache_read_tokens, i.cache_creation_tokens
        FROM IO_TOKENS i
        JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
        WHERE up.session_id = ?
        ORDER BY up.timestamp ASC
    """, (session_id,))


def get_session_prompt_order(session_id: str):
    rows = q("SELECT prompt_id FROM USER_PROMPT WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    return {r["prompt_id"]: i + 1 for i, r in enumerate(rows)}


def get_project_io_totals(project_id: str):
    return q("""
        SELECT COALESCE(SUM(i.input_tokens),0) AS inp,
               COALESCE(SUM(i.output_tokens),0) AS out,
               COALESCE(SUM(i.cache_read_tokens),0) AS cr,
               COALESCE(SUM(i.cache_creation_tokens),0) AS cc
        FROM IO_TOKENS i JOIN USER_PROMPT up ON up.prompt_id=i.prompt_id
        JOIN SESSION s ON s.session_id=up.session_id
        WHERE s.project_id=?
    """, (project_id,), one=True)


def get_project_tool_totals(project_id: str):
    return q("""
        SELECT SUM(tt.input_tokens) AS inp,
               SUM(tt.output_tokens) AS out,
               SUM(tt.cache_read_tokens) AS cr,
               SUM(tt.cache_creation_tokens) AS cc
        FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
        JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
        JOIN SESSION s ON s.session_id=up.session_id
        WHERE s.project_id=?
    """, (project_id,), one=True)


def get_project_sessions_token_breakdown(project_id: str):
    return q("""
        SELECT s.session_id, substr(s.created_at,1,10) AS session_date,
               COALESCE(io.inp,0) AS io_input, COALESCE(io.out,0) AS io_output,
               COALESCE(io.cr,0)  AS io_cr,    COALESCE(io.cc,0)  AS io_cc,
               tl.inp AS tool_input, tl.out AS tool_output,
               tl.cr  AS tool_cr,    tl.cc  AS tool_cc
        FROM SESSION s
        LEFT JOIN (
            SELECT up.session_id,
                   SUM(i.input_tokens) AS inp, SUM(i.output_tokens) AS out,
                   SUM(i.cache_read_tokens) AS cr, SUM(i.cache_creation_tokens) AS cc
            FROM IO_TOKENS i JOIN USER_PROMPT up ON up.prompt_id=i.prompt_id
            GROUP BY up.session_id
        ) io ON io.session_id=s.session_id
        LEFT JOIN (
            SELECT up.session_id,
                   SUM(tt.input_tokens) AS inp, SUM(tt.output_tokens) AS out,
                   SUM(tt.cache_read_tokens) AS cr, SUM(tt.cache_creation_tokens) AS cc
            FROM TOOL_TOKENS tt JOIN TOOL t ON t.tool_id=tt.tool_id
            JOIN USER_PROMPT up ON up.prompt_id=t.prompt_id
            GROUP BY up.session_id
        ) tl ON tl.session_id=s.session_id
        WHERE s.project_id=?
        ORDER BY (COALESCE(io.inp,0)+COALESCE(tl.inp,0)) DESC
    """, (project_id,))