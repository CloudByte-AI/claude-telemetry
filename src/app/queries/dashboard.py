"""All SQL queries related to the dashboard."""

from ..routers.db import q


def get_dashboard_stats():
    return q("""
        SELECT
            (SELECT COUNT(*) FROM PROJECT)  AS total_projects,
            (SELECT COUNT(*) FROM SESSION)  AS total_sessions,
            (SELECT COUNT(*) FROM USER_PROMPT) AS total_prompts,
            (SELECT COUNT(*) FROM TOOL)     AS total_tools,
            (SELECT COUNT(DISTINCT tool_name) FROM TOOL) AS unique_tools,
            (SELECT COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)
             FROM IO_TOKENS) AS total_tokens,
            (SELECT COUNT(*) FROM HOOK_OBSERVATION) AS total_observations
    """, one=True)


def get_recent_sessions(limit: int = 5):
    return q("""
        SELECT s.session_id, s.cwd, s.created_at,
               p.name                       AS project_name,
               COUNT(DISTINCT up.prompt_id) AS prompt_count,
               COUNT(DISTINCT t.tool_id)    AS tool_count
        FROM SESSION s
        LEFT JOIN PROJECT p      ON p.project_id = s.project_id
        LEFT JOIN USER_PROMPT up ON up.session_id = s.session_id
        LEFT JOIN TOOL t         ON t.prompt_id   = up.prompt_id
        GROUP BY s.session_id
        ORDER BY s.created_at DESC
        LIMIT ?
    """, (limit,))


def get_activity_heatmap():
    """Day-level activity: prompts per calendar day."""
    return q("""
        SELECT substr(up.timestamp,1,10) AS day,
               COUNT(*)                  AS prompts
        FROM USER_PROMPT up
        GROUP BY day
        ORDER BY day
    """)


def get_projects_radar():
    """Per-project: sessions, prompts, observations — the 3 edges."""
    return q("""
        SELECT p.name                          AS project_name,
               COUNT(DISTINCT s.session_id)    AS sessions,
               COUNT(DISTINCT up.prompt_id)    AS prompts,
               COUNT(DISTINCT o.id)            AS observations
        FROM PROJECT p
        LEFT JOIN SESSION s      ON s.project_id  = p.project_id
        LEFT JOIN USER_PROMPT up ON up.session_id  = s.session_id
        LEFT JOIN HOOK_OBSERVATION o  ON o.session_id   = s.session_id
        GROUP BY p.project_id
        ORDER BY sessions DESC
    """)


def get_obs_types_per_project():
    """Per-project observation type counts — for observation radar."""
    return q("""
        SELECT p.name  AS project_name,
               o.type  AS obs_type,
               COUNT(*) AS count
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.type IS NOT NULL
        GROUP BY p.project_id, o.type
    """)


def get_latest_observations(limit: int = 2):
    return q("""
        SELECT o.id, o.title, o.subtitle, o.type, o.created_at, o.session_id,
               p.name AS project_name
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        ORDER BY o.created_at DESC
        LIMIT ?
    """, (limit,))