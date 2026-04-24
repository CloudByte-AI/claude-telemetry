"""All SQL queries related to projects."""

from ..routers.db import q


def get_all_projects():
    return q("""
        SELECT p.project_id, p.name, p.path, p.created_at,
               COUNT(DISTINCT s.session_id)  AS session_count,
               COUNT(DISTINCT up.prompt_id)  AS prompt_count,
               COUNT(DISTINCT t.tool_id)     AS tool_count,
               COUNT(DISTINCT t.tool_name)   AS unique_tools,
               COALESCE(SUM(i.input_tokens),0)+COALESCE(SUM(i.output_tokens),0)+
               COALESCE(SUM(i.cache_read_tokens),0)+
               COALESCE(SUM(i.cache_creation_tokens),0) AS io_tokens,
               MAX(substr(s.created_at,1,10)) AS last_session
        FROM PROJECT p
        LEFT JOIN SESSION s      ON s.project_id  = p.project_id
        LEFT JOIN USER_PROMPT up ON up.session_id  = s.session_id
        LEFT JOIN TOOL t         ON t.prompt_id    = up.prompt_id
        LEFT JOIN IO_TOKENS i    ON i.prompt_id    = up.prompt_id
        GROUP BY p.project_id
        ORDER BY p.created_at DESC
    """)


def get_project(project_id: str):
    return q("SELECT * FROM PROJECT WHERE project_id=?", (project_id,), one=True)