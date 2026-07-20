"""Template rendering helpers for package-provided server commands."""

from __future__ import annotations

import ast
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


class GeneratedServerSyntaxError(ValueError):
    """Raised when rendered server source is not valid Python syntax."""

    def __init__(self, template_name: str, *, line: int, column: int) -> None:
        self.template_name = template_name
        self.line = line
        self.column = column
        super().__init__(
            f"generated server template {template_name!r} is invalid at "
            f"line {line}, column {column}"
        )


def render_python_command(template_name: str, **context: Any) -> tuple[str, ...]:
    """Render a packaged Python server template as a Docker command."""

    template_text = resources.files(__package__).joinpath("templates", template_name).read_text()
    script = _ENVIRONMENT.from_string(template_text).render(**context)
    return validated_python_command(script, template_name=template_name)


def validated_python_command(script: str, *, template_name: str) -> tuple[str, ...]:
    """Return a Python command only after syntax validation succeeds."""

    failure: GeneratedServerSyntaxError | None = None
    try:
        ast.parse(script, filename=f"<{template_name}>")
    except SyntaxError as error:
        failure = GeneratedServerSyntaxError(
            template_name,
            line=error.lineno or 0,
            column=error.offset or 0,
        )
    if failure is not None:
        raise failure
    return ("python", "-c", script)
