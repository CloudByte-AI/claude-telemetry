"""Security scanning UI routes."""

import logging
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..routers.dependencies import templates
from ..services import security as svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/security", response_class=HTMLResponse)
def security_page(request: Request):
    ctx = svc.get_security_context()
    return templates.TemplateResponse(request=request, name="security/security.html", context=ctx)


@router.post("/security", response_class=HTMLResponse)
async def security_save(request: Request):
    form_data = await request.form()
    form = dict(form_data)
    success, message = svc.save_from_form(form)
    ctx = svc.get_security_context()
    ctx["save_success"] = success
    ctx["save_message"] = message
    return templates.TemplateResponse(request=request, name="security/security.html", context=ctx)


@router.post("/security/preset/{name}", response_class=JSONResponse)
async def apply_preset(name: str, request: Request):
    if name not in ("minimal", "standard", "strict"):
        return JSONResponse({"ok": False, "error": "Unknown preset"}, status_code=400)
    success, message = svc.apply_preset(name)
    return JSONResponse({"ok": success, "message": message})


@router.get("/security/events", response_class=HTMLResponse)
def security_events(
    request: Request,
    period: str = "7d",
    target: str = "",
    blocked: str = "",
    page: int = 1,
    client: str = "all",
):
    try:
        ctx = svc.get_events_context(
            period=period,
            scan_target=target or None,
            blocked_only=(blocked == "1"),
            page=page,
            client=client,
        )
        return templates.TemplateResponse(request=request, name="security/events.html", context=ctx)
    except Exception:
        logger.exception("Failed to render security events page")
        return RedirectResponse("/security", status_code=302)


@router.post("/security/api/generate-pattern", response_class=JSONResponse)
async def generate_pattern(request: Request):
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        examples = [str(e).strip() for e in (body.get("examples") or []) if str(e).strip()]
        severity = (body.get("severity") or "HIGH").upper()
        if not name:
            return JSONResponse({"ok": False, "error": "Name is required."}, status_code=400)
        if not examples:
            return JSONResponse({"ok": False, "error": "At least one example is required."}, status_code=400)
        result = svc.generate_pattern_preview(name, examples, severity)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/security/api/add-pattern", response_class=JSONResponse)
async def add_pattern(request: Request):
    try:
        body = await request.json()
        name     = (body.get("name") or "").strip()
        pattern  = (body.get("pattern") or "").strip()
        examples = body.get("examples") or []
        severity = (body.get("severity") or "HIGH").upper()
        if not name:
            return JSONResponse({"ok": False, "error": "Name is required."}, status_code=400)
        success, message = svc.add_custom_pattern(name, pattern, examples, severity)
        return JSONResponse({"ok": success, "message": message})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.put("/security/api/pattern/{old_name}", response_class=JSONResponse)
async def update_pattern(old_name: str, request: Request):
    try:
        body     = await request.json()
        name     = (body.get("name") or "").strip()
        pattern  = (body.get("pattern") or "").strip()
        examples = body.get("examples") or []
        severity = (body.get("severity") or "HIGH").upper()
        if not name:
            return JSONResponse({"ok": False, "error": "Name is required."}, status_code=400)
        success, message = svc.update_custom_pattern(old_name, name, pattern, examples, severity)
        return JSONResponse({"ok": success, "message": message})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.delete("/security/api/pattern/{name}", response_class=JSONResponse)
async def remove_pattern(name: str, request: Request):
    success, message = svc.remove_custom_pattern(name)
    return JSONResponse({"ok": success, "message": message})


@router.get("/security/api/status", response_class=JSONResponse)
def security_status(request: Request):
    """Used by the dashboard card."""
    try:
        cfg = svc.load_security_yaml()
        from ..queries.security import get_scan_stats
        stats = get_scan_stats()
        return JSONResponse({
            "enabled": bool(cfg.get("enabled", False)),
            "plan":    cfg.get("plan", "standard"),
            **stats,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
