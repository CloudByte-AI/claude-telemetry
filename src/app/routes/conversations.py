from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..routers.dependencies import templates
from ..services import sessions as svc

router = APIRouter()


@router.get("/conversation/{prompt_id}", response_class=HTMLResponse)
def conversation_detail(request: Request, prompt_id: str):
    ctx = svc.get_conversation_context(prompt_id)
    if not ctx:
        return HTMLResponse("<h1>Conversation not found</h1>", status_code=404)
    return templates.TemplateResponse(request=request, name="sessions/conversation.html", context=ctx)