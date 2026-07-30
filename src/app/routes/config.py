from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import logging

from ..routers.dependencies import templates
from ..services import config as svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    ctx = svc.get_config_context()
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)


@router.get("/config/cleanup_preview", response_class=JSONResponse)
def database_cleanup_preview(request: Request):
    stats = svc.preview_database_cleanup()
    return stats


@router.post("/config/cleanup", response_class=JSONResponse)
async def database_cleanup(request: Request):
    stats = svc.run_database_cleanup()
    return stats


@router.get("/config/logcleanup_preview", response_class=JSONResponse)
def log_cleanup_preview(request: Request):
    return {"count": svc.count_old_log_files()}


@router.post("/config/logcleanup", response_class=JSONResponse)
async def log_cleanup(request: Request):
    deleted = svc.run_log_cleanup()
    return {"deleted": deleted}


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request):
    form_data = await request.form()
    form      = dict(form_data)
    success, message = svc.update_config(form)
    ctx = svc.get_config_context()
    ctx["save_success"] = success
    ctx["save_message"] = message
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)