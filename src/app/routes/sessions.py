from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import sessions as svc

router = APIRouter()


@router.get("/sessions", response_class=HTMLResponse)
def sessions_list(request: Request, search: str = "", page: int = 1, per_page: int = 10):
    ctx = svc.get_sessions_list_context(search, page, per_page)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="sessions/_table.html", context=ctx)
    return templates.TemplateResponse(request=request, name="sessions/list.html", context=ctx)


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(request: Request, session_id: str):
    ctx = svc.get_session_detail_context(session_id)
    if not ctx:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="sessions/detail.html", context=ctx)