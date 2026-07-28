from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import projects as svc

router = APIRouter()


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request, client: str = "all"):
    ctx = svc.get_projects_context(client)
    return templates.TemplateResponse(request=request, name="projects/projects.html", context=ctx)