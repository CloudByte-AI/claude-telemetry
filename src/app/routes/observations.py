from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import observations as svc

router = APIRouter()


@router.get("/observations", response_class=HTMLResponse)
def observations(
    request:     Request,
    search:      str = "",
    type_filter: str = "",
    date_from:   str = "",
    date_to:     str = "",
    page:        int = 1,
):
    ctx = svc.get_observations_context(search, type_filter, date_from, date_to, page)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="memory/_obs_content.html", context=ctx)
    return templates.TemplateResponse(request=request, name="memory/observations.html", context=ctx)


@router.get("/observations/{obs_id}", response_class=HTMLResponse)
def observation_detail(request: Request, obs_id: str):
    ctx = svc.get_observation_detail_context(obs_id)
    if not ctx:
        return HTMLResponse("<h1>Observation not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="memory/observation_detail.html", context=ctx)