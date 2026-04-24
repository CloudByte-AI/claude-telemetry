from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..routers.dependencies import templates
from ..services import config as svc

router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    ctx = svc.get_config_context()
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request):
    form_data = await request.form()
    form      = dict(form_data)
    success, message = svc.update_config(form)
    ctx = svc.get_config_context()
    ctx["save_success"] = success
    ctx["save_message"] = message
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)