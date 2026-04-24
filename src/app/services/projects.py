"""Business logic for the projects page."""

from ..queries import projects as pq


def get_projects_context() -> dict:
    return {
        "active":   "projects",
        "projects": pq.get_all_projects(),
    }