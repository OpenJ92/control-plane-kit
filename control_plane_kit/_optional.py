"""Small diagnostics for explicit optional package surfaces."""

from __future__ import annotations

from importlib.util import find_spec


def require_optional_dependencies(
    surface: str,
    dependencies: tuple[str, ...],
    *,
    extra: str,
) -> None:
    """Fail before importing an optional surface with an actionable message."""

    missing = tuple(name for name in dependencies if find_spec(name) is None)
    if not missing:
        return
    rendered = ", ".join(missing)
    raise ModuleNotFoundError(
        f"{surface} requires optional dependencies: {rendered}. "
        f"Install control-plane-kit[{extra}] before importing this surface."
    )
