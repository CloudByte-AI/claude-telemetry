"""
dependencies.py — Shared FastAPI dependencies.

The templates instance is created here once and imported by all route modules.
This avoids circular imports since app.py imports routes and routes need templates.
"""

import markdown as md
from pathlib import Path
from fastapi.templating import Jinja2Templates

BASE_DIR  = Path(__file__).parent.parent

# Create templates with custom filters
def markdown_filter(text: str) -> str:
    """Convert markdown text to HTML."""
    if not text:
        return ""
    # Enable safe extensions and escape HTML
    return md.markdown(text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters['markdown'] = markdown_filter