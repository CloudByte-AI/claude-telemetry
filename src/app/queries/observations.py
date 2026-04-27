"""All SQL queries for observations and session summaries."""

from ..routers.db import q


# ── Observations ───────────────────────────────────────────────────────────────

def get_observations_list(search: str = "", type_filter: str = "", date_from: str = "", date_to: str = ""):
    filters = ["1=1"]
    params: list = []

    if search.strip():
        filters.append("(o.title LIKE ? OR o.subtitle LIKE ? OR o.narrative LIKE ? OR p.name LIKE ? OR o.session_id LIKE ?)")
        like = f"%{search.strip()}%"
        params += [like, like, like, like, like]

    if type_filter and type_filter != "all":
        filters.append("o.type = ?")
        params.append(type_filter)

    if date_from:
        filters.append("substr(o.created_at,1,10) >= ?")
        params.append(date_from)
    if date_to:
        filters.append("substr(o.created_at,1,10) <= ?")
        params.append(date_to)

    where = " AND ".join(filters)
    return q(f"""
        SELECT o.id, o.session_id, o.prompt_id,
               o.type, o.title, o.subtitle, o.narrative,
               o.facts, o.concepts, o.files_read, o.files_modified,
               o.created_at,
               p.name AS project_name, p.project_id,
               (
                   SELECT COUNT(*) FROM USER_PROMPT up2
                   WHERE up2.session_id = o.session_id
                   AND up2.timestamp <= (
                       SELECT timestamp FROM USER_PROMPT up3
                       WHERE up3.prompt_id = o.prompt_id
                   )
               ) AS turn_number
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s  ON s.session_id = o.session_id
        LEFT JOIN PROJECT p  ON p.project_id = s.project_id
        WHERE {where}
        ORDER BY o.created_at DESC
    """, tuple(params))




def get_bubble_chart_data(date_from: str = "", date_to: str = ""):
    """Returns per-session per-type observation counts for bubble chart."""
    filters = ["1=1"]
    params: list = []
    if date_from:
        filters.append("substr(o.created_at,1,10) >= ?")
        params.append(date_from)
    if date_to:
        filters.append("substr(o.created_at,1,10) <= ?")
        params.append(date_to)
    where = " AND ".join(filters)
    return q(f"""
        SELECT o.session_id, o.type,
               COUNT(*) AS count,
               p.name   AS project_name,
               substr(o.created_at,1,10) AS obs_date
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE {where}
        GROUP BY o.session_id, o.type
        ORDER BY count DESC
    """, tuple(params))
def get_observation(obs_id: str):
    return q("""
        SELECT o.id, o.session_id, o.prompt_id,
               o.type, o.title, o.subtitle, o.narrative, o.text,
               o.facts, o.concepts, o.files_read, o.files_modified,
               o.content_hash, o.created_at,
               p.name   AS project_name,
               p.project_id,
               s.created_at AS session_started,
               s.cwd,
               (
                   SELECT COUNT(*) FROM USER_PROMPT up2
                   WHERE up2.session_id = o.session_id
                   AND up2.timestamp <= (
                       SELECT timestamp FROM USER_PROMPT up3
                       WHERE up3.prompt_id = o.prompt_id
                   )
               ) AS turn_number
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.id = ?
    """, (obs_id,), one=True)


def get_observations_stats() -> dict:
    row = q("""
        SELECT
            COUNT(*)                                     AS total,
            COUNT(DISTINCT session_id)                   AS sessions,
            COUNT(CASE WHEN type='bugfix'    THEN 1 END) AS bugfix,
            COUNT(CASE WHEN type='feature'   THEN 1 END) AS feature,
            COUNT(CASE WHEN type='refactor'  THEN 1 END) AS refactor,
            COUNT(CASE WHEN type='change'    THEN 1 END) AS change,
            COUNT(CASE WHEN type='discovery' THEN 1 END) AS discovery,
            COUNT(CASE WHEN type='decision'  THEN 1 END) AS decision
        FROM HOOK_OBSERVATION
    """, one=True)
    if row:
        return dict(row)
    return {"total": 0, "sessions": 0, "bugfix": 0, "feature": 0,
            "refactor": 0, "change": 0, "discovery": 0, "decision": 0}


def get_observation_type_counts():
    return q("""
        SELECT type, COUNT(*) AS count
        FROM HOOK_OBSERVATION
        WHERE type IS NOT NULL
        GROUP BY type
        ORDER BY count DESC
    """)


def get_session_observations_full(session_id: str):
    return q("""
        SELECT id, session_id, prompt_id, type, title, subtitle,
               narrative, text, facts, concepts,
               files_read, files_modified, content_hash, created_at
        FROM HOOK_OBSERVATION
        WHERE session_id = ?
        ORDER BY created_at ASC
    """, (session_id,))


def get_nearby_observations(session_id: str, limit: int = 10):
    return q("""
        SELECT id, type, title, subtitle, created_at
        FROM HOOK_OBSERVATION
        WHERE session_id = ?
        ORDER BY created_at ASC
        LIMIT ?
    """, (session_id, limit))


def get_session_task_counts():
    """Get pending/failed task counts for all sessions."""
    return q("""
        SELECT
            session_id,
            COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending,
            COUNT(CASE WHEN status = 'running' THEN 1 END) AS running,
            COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed
        FROM TASK_QUEUE
        GROUP BY session_id
    """)