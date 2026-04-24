from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import dashboard as svc

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    ctx = svc.get_dashboard_context()
    return templates.TemplateResponse(request=request, name="dashboard/dashboard.html", context=ctx)