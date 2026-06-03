from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import logging

from ..routers.dependencies import templates
from ..services import config as svc
from src.common.paths import get_cloudbyte_dir

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Upload constraints ──────────────────────────────────────────────────────
ALLOWED_SOUND_EXTENSIONS = (".wav", ".mp3")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_UPLOAD_BYTES = 64                # reject obviously-empty / truncated files

# Magic-byte signatures for audio formats we accept
_WAV_MAGIC   = b"RIFF"  # RIFF....WAVE
_WAV_WAVE    = b"WAVE"  # bytes 8-12
_MP3_MAGIC   = [
    b"\xff\xfb",   # MPEG1 Layer3, no ID3
    b"\xff\xf3",   # MPEG2 Layer3
    b"\xff\xf2",   # MPEG2.5 Layer3
    b"ID3",        # ID3v2 tag (common MP3 header)
]

# Allowed built-in sound names that can be selected in the form
ALLOWED_SOUND_NAMES = {"chime", "soft", "urgent"}
# sound_source values the form may submit
ALLOWED_SOUND_SOURCES = ALLOWED_SOUND_NAMES | {"custom"}


def get_worker_status():
    """Get worker status directly from worker state."""
    try:
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
            "worker_exited": True,
        }


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    ctx = svc.get_config_context()
    ctx["worker_status"] = get_worker_status()
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


@router.get("/config/preview_sound")
def preview_sound():
    """Serve the saved custom alert WAV so the browser can play it via a URL."""
    from fastapi import HTTPException
    wav_path = get_cloudbyte_dir() / "sounds" / "custom_alerts" / "custom_alert.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="No custom alert sound saved yet.")
    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename="custom_alert.wav",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/config/preview_builtin/{name}")
def preview_builtin_sound(name: str):
    """Serve one of the shipped built-in WAV files for in-browser preview."""
    from fastapi import HTTPException
    from pathlib import Path
    # Strict allowlist — no path traversal possible
    if name not in ALLOWED_SOUND_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown sound name: {name!r}")
    wav_path = Path(__file__).parent.parent.parent / "sounds" / f"{name}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail=f"Built-in sound '{name}' not found on disk.")
    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{name}.wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request):
    try:
        return await _config_save_inner(request)
    except Exception as exc:
        logger.exception("Unhandled error in config_save")
        ctx = svc.get_config_context()
        ctx["save_success"] = False
        ctx["save_message"] = f"Internal error: {exc}"
        ctx["worker_status"] = get_worker_status()
        return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)


async def _config_save_inner(request: Request):
    form_data = await request.form()
    form_dict = dict(form_data)

    # ── Validate sound_source ──────────────────────────────────────────────
    # Single field drives both alert_sound and alert_sound_name.
    sound_source = form_dict.get("sound_source", "").strip().lower()
    if sound_source not in ALLOWED_SOUND_SOURCES:
        ctx = svc.get_config_context()
        ctx["save_success"] = False
        ctx["save_message"] = (
            f"Error: '{sound_source}' is not a valid sound source. "
            f"Allowed values: {', '.join(sorted(ALLOWED_SOUND_SOURCES))}."
        )
        ctx["worker_status"] = get_worker_status()
        return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)

    # ── Custom alert sound upload (only when source == 'custom') ───────────
    if sound_source == "custom" and "custom_alert_file" in form_data:
        upload_file = form_data["custom_alert_file"]
        if hasattr(upload_file, "filename") and upload_file.filename:
            import os as _os
            safe_name = _os.path.basename(upload_file.filename).lower()

            if not safe_name.endswith(ALLOWED_SOUND_EXTENSIONS):
                ctx = svc.get_config_context()
                ctx["save_success"] = False
                ctx["save_message"] = "Error: Only WAV or MP3 files are accepted."
                ctx["worker_status"] = get_worker_status()
                return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)

            content = await upload_file.read()

            if len(content) < MIN_UPLOAD_BYTES:
                ctx = svc.get_config_context()
                ctx["save_success"] = False
                ctx["save_message"] = "Error: Uploaded file is empty or too small."
                ctx["worker_status"] = get_worker_status()
                return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)

            if len(content) > MAX_UPLOAD_BYTES:
                ctx = svc.get_config_context()
                ctx["save_success"] = False
                ctx["save_message"] = "Error: File is too large. Maximum allowed size is 10 MB."
                ctx["worker_status"] = get_worker_status()
                return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)

            if not _is_valid_audio(content, safe_name):
                ctx = svc.get_config_context()
                ctx["save_success"] = False
                ctx["save_message"] = (
                    "Error: File does not appear to be a valid WAV or MP3 audio file."
                )
                ctx["worker_status"] = get_worker_status()
                return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)

            custom_sounds_dir = get_cloudbyte_dir() / "sounds" / "custom_alerts"
            custom_sounds_dir.mkdir(parents=True, exist_ok=True)
            wav_path = custom_sounds_dir / "custom_alert.wav"
            wav_path.write_bytes(content)
            logger.info(f"Custom alert sound saved ({len(content)} bytes) → {wav_path}")
            form_dict["_alert_sound_path"] = str(wav_path.resolve())

    form_dict.pop("custom_alert_file", None)

    success, message = svc.update_config(form_dict)

    ctx = svc.get_config_context()
    ctx["save_success"] = success
    ctx["save_message"] = message
    ctx["worker_status"] = get_worker_status()
    return templates.TemplateResponse(request=request, name="config/config.html", context=ctx)


def _is_valid_audio(content: bytes, filename: str) -> bool:
    """
    Validate uploaded audio by inspecting the first few magic bytes.
    Returns True only if the content matches a known-safe audio signature.
    """
    if filename.endswith(".wav"):
        # RIFF....WAVE signature
        return (
            len(content) >= 12
            and content[:4] == _WAV_MAGIC
            and content[8:12] == _WAV_WAVE
        )
    if filename.endswith(".mp3"):
        return any(content[:len(sig)] == sig for sig in _MP3_MAGIC)
    return False