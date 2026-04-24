"""
dependencies.py — Shared FastAPI dependencies.

The templates instance is created here once and imported by all route modules.
This avoids circular imports since app.py imports routes and routes need templates.
"""

from pathlib import Path
from fastapi.templating import Jinja2Templates

BASE_DIR  = Path(__file__).parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")