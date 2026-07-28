"""Business logic for the projects page."""

from ..queries import projects as pq


def get_projects_context(client: str = None) -> dict:
    # get_all_projects() always computes the sessions_claude_code/sessions_cursor
    # breakdown columns across ALL clients by design (same reasoning as
    # queries/dashboard.py get_projects_radar) - it is not filtered here.
    # `client` is still threaded through so the topbar filter value is available
    # to the template for any future links; the projects grid has no pagination
    # or search to preserve it across today.
    return {
        "active":       "projects",
        "client_filter": client or "all",
        "projects":     pq.get_all_projects(),
    }