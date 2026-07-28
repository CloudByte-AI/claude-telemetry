"""All SQL queries related to the dashboard."""

from ..routers.db import q, client_where


def get_dashboard_stats(client: str = None):
    """Headline counts. total_projects stays unfiltered (a project can span both
    clients); every other count is scoped to sessions matching the filter."""
    where, params = client_where(client, "s")
    return q(f"""
        SELECT
            (SELECT COUNT(*) FROM PROJECT)  AS total_projects,
            (SELECT COUNT(*) FROM SESSION s WHERE 1=1 {where})  AS total_sessions,
            (SELECT COUNT(*) FROM USER_PROMPT up
                JOIN SESSION s ON s.session_id = up.session_id
                WHERE 1=1 {where}) AS total_prompts,
            (SELECT COUNT(*) FROM TOOL t
                JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
                JOIN SESSION s ON s.session_id = up.session_id
                WHERE 1=1 {where}) AS total_tools,
            (SELECT COUNT(DISTINCT t.tool_name) FROM TOOL t
                JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id
                JOIN SESSION s ON s.session_id = up.session_id
                WHERE 1=1 {where}) AS unique_tools,
            (SELECT COALESCE(SUM(i.input_tokens+i.output_tokens+i.cache_read_tokens+i.cache_creation_tokens),0)
                FROM IO_TOKENS i
                JOIN USER_PROMPT up ON up.prompt_id = i.prompt_id
                JOIN SESSION s ON s.session_id = up.session_id
                WHERE 1=1 {where}) AS total_tokens,
            (SELECT COUNT(*) FROM HOOK_OBSERVATION o
                JOIN SESSION s ON s.session_id = o.session_id
                WHERE 1=1 {where}) AS total_observations
    """, params * 6, one=True)


def get_recent_sessions(limit: int = 5, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT s.session_id, s.cwd, s.created_at, s.client,
               p.name                       AS project_name,
               COUNT(DISTINCT up.prompt_id) AS prompt_count,
               COUNT(DISTINCT t.tool_id)    AS tool_count
        FROM SESSION s
        LEFT JOIN PROJECT p      ON p.project_id = s.project_id
        LEFT JOIN USER_PROMPT up ON up.session_id = s.session_id
        LEFT JOIN TOOL t         ON t.prompt_id   = up.prompt_id
        WHERE 1=1 {where}
        GROUP BY s.session_id
        ORDER BY s.created_at DESC
        LIMIT ?
    """, params + (limit,))


def get_activity_heatmap(client: str = None):
    """Day-level activity: prompts per calendar day."""
    where, params = client_where(client, "s")
    return q(f"""
        SELECT substr(up.timestamp,1,10) AS day,
               COUNT(*)                  AS prompts
        FROM USER_PROMPT up
        JOIN SESSION s ON s.session_id = up.session_id
        WHERE 1=1 {where}
        GROUP BY day
        ORDER BY day
    """, params)


def get_activity_heatmap_by_client():
    """Day-level activity broken down by client, for the dual-color heatmap treatment.

    Deliberately NOT filtered by the global client selector - it exists precisely
    to show the client split, so it always reflects all clients regardless of
    the current filter.
    """
    return q("""
        SELECT substr(up.timestamp,1,10) AS day,
               s.client                  AS client,
               COUNT(*)                  AS prompts
        FROM USER_PROMPT up
        JOIN SESSION s ON s.session_id = up.session_id
        GROUP BY day, client
        ORDER BY day
    """)


def get_projects_radar(client: str = None):
    """Per-project: sessions, prompts, observations — the 3 edges.

    The per-client breakdown columns (sessions_claude_code/sessions_cursor/etc.)
    are always computed across all clients regardless of the filter, same
    reasoning as get_activity_heatmap_by_client - they exist to show the split.
    The main sessions/prompts/observations columns respect the filter.
    """
    where, params = client_where(client, "s")
    return q(f"""
        SELECT p.name                          AS project_name,
               COUNT(DISTINCT s.session_id)    AS sessions,
               COUNT(DISTINCT up.prompt_id)    AS prompts,
               COUNT(DISTINCT o.id)            AS observations,
               COUNT(DISTINCT CASE WHEN s.client = 'claude_code' THEN s.session_id END) AS sessions_claude_code,
               COUNT(DISTINCT CASE WHEN s.client = 'cursor'      THEN s.session_id END) AS sessions_cursor,
               COUNT(DISTINCT CASE WHEN s.client = 'claude_code' THEN up.prompt_id END) AS prompts_claude_code,
               COUNT(DISTINCT CASE WHEN s.client = 'cursor'      THEN up.prompt_id END) AS prompts_cursor,
               COUNT(DISTINCT CASE WHEN s.client = 'claude_code' THEN o.id END)         AS observations_claude_code,
               COUNT(DISTINCT CASE WHEN s.client = 'cursor'      THEN o.id END)         AS observations_cursor
        FROM PROJECT p
        LEFT JOIN SESSION s      ON s.project_id  = p.project_id {where}
        LEFT JOIN USER_PROMPT up ON up.session_id  = s.session_id
        LEFT JOIN HOOK_OBSERVATION o  ON o.session_id   = s.session_id
        GROUP BY p.project_id
        ORDER BY sessions DESC
    """, params)


def get_obs_types_per_project(client: str = None):
    """Per-project observation type counts — for observation radar."""
    where, params = client_where(client, "s")
    return q(f"""
        SELECT p.name  AS project_name,
               o.type  AS obs_type,
               COUNT(*) AS count
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.type IS NOT NULL {where}
        GROUP BY p.project_id, o.type
    """, params)


def get_latest_observations(limit: int = 2, client: str = None):
    where, params = client_where(client, "s")
    return q(f"""
        SELECT o.id, o.title, o.subtitle, o.type, o.created_at, o.session_id,
               p.name AS project_name
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE 1=1 {where}
        ORDER BY o.created_at DESC
        LIMIT ?
    """, params + (limit,))