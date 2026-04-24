from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import tools as svc

router = APIRouter()


@router.get("/tools", response_class=HTMLResponse)
def tool_calls(
    request:      Request,
    dr:           str = "all",
    date_from:    str = "",
    date_to:      str = "",
    tool_search:  str = "",
    sess_search:  str = "",
    proj_search:  str = "",
    tool_page:    int = 1,
    sess_page:    int = 1,
    proj_page:    int = 1,
    per_page:     int = 10,
):
    ctx    = svc.get_tools_page_context(dr, date_from, date_to, tool_search, sess_search, proj_search, tool_page, sess_page, proj_page, per_page)
    target = request.headers.get("HX-Target", "")
    if request.headers.get("HX-Request"):
        if target == "tool-table-wrap":
            return templates.TemplateResponse(request=request, name="analytics/_tools_tool.html", context=ctx)
        elif target == "sess-table-wrap":
            return templates.TemplateResponse(request=request, name="analytics/_tools_sess.html", context=ctx)
        elif target == "proj-table-wrap":
            return templates.TemplateResponse(request=request, name="analytics/_tools_proj.html", context=ctx)
    return templates.TemplateResponse(request=request, name="analytics/tools.html", context=ctx)


@router.get("/tools/session/{session_id}", response_class=HTMLResponse)
def session_tool_analysis(request: Request, session_id: str):
    ctx = svc.get_session_tool_context(session_id)
    if not ctx:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="analytics/session_tools.html", context=ctx)


@router.get("/tools/project/{project_id}", response_class=HTMLResponse)
def project_tool_analysis(request: Request, project_id: str):
    ctx = svc.get_project_tool_context(project_id)
    if not ctx:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="analytics/project_tools.html", context=ctx)