from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import tokens as svc

router = APIRouter()


@router.get("/tokens", response_class=HTMLResponse)
def token_usage(
    request:     Request,
    dr:          str = "all",
    date_from:   str = "",
    date_to:     str = "",
    sess_search: str = "",
    proj_search: str = "",
    sess_page:   int = 1,
    proj_page:   int = 1,
    per_page:    int = 10,
):
    ctx    = svc.get_token_usage_context(dr, date_from, date_to, sess_search, proj_search, sess_page, proj_page, per_page)
    target = request.headers.get("HX-Target", "")
    if request.headers.get("HX-Request"):
        if target == "sess-table-wrap":
            return templates.TemplateResponse(request=request, name="analytics/_tokens_sess.html", context=ctx)
        elif target == "proj-table-wrap":
            return templates.TemplateResponse(request=request, name="analytics/_tokens_proj.html", context=ctx)
    return templates.TemplateResponse(request=request, name="analytics/tokens.html", context=ctx)


@router.get("/tokens/session/{session_id}", response_class=HTMLResponse)
def session_token_analysis(request: Request, session_id: str):
    ctx = svc.get_session_token_context(session_id)
    if not ctx:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="analytics/session_tokens.html", context=ctx)


@router.get("/tokens/project/{project_id}", response_class=HTMLResponse)
def project_token_analysis(request: Request, project_id: str):
    ctx = svc.get_project_token_context(project_id)
    if not ctx:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="analytics/project_tokens.html", context=ctx)