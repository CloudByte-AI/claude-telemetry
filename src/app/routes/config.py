from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import logging

from ..routers.dependencies import templates
from ..services import config as svc

logger = logging.getLogger(__name__)
router = APIRouter()


def get_worker_status():
    """Get worker status directly from worker state."""
    try:
        # Import here to avoid circular imports
        from .worker import get_worker_state
        worker_state = get_worker_state()
        return worker_state.get_status()
    except Exception as e:
        logger.warning(f"Failed to get worker status: {e}")
        return {
            "running": False,
            "pending_tasks": 0,
            "running_tasks": 0,
            "shutdown_requested": False,
            "thread_alive": False,
            "worker_exited": True
        }


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    ctx = svc.get_config_context()
    ctx["worker_status"] = get_worker_status()
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request):
    form_data = await request.form()
    form      = dict(form_data)
    success, message = svc.update_config(form)
    ctx = svc.get_config_context()
    ctx["save_success"] = success
    ctx["save_message"] = message
    ctx["worker_status"] = get_worker_status()
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)