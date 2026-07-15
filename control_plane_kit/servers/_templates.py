"""Template rendering helpers for package-provided server commands."""

from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined


_ENVIRONMENT = Environment(
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)


def render_python_command(template_name: str, **context: Any) -> tuple[str, ...]:
    """Render a packaged Python server template as a Docker command."""

    template_text = resources.files(__package__).joinpath("templates", template_name).read_text()
    script = _ENVIRONMENT.from_string(template_text).render(**context)
    return ("python", "-c", script)
