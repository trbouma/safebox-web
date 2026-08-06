"""Jinja2 environment for Safebox Web's server-rendered pages."""

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates


TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))


def render_template(template_name: str, **context: Any) -> str:
    """Render a complete HTML representation without introducing browser state."""

    return templates.env.get_template(template_name).render(**context)
