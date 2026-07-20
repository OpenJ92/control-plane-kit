"""Pure strict rendering of typed product configuration into artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Mapping, Protocol, TypeAlias

from jinja2 import StrictUndefined, TemplateError, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from control_plane_kit.core.configuration import (
    MAX_CONFIGURATION_BYTES,
    ConfigurationArtifact,
    ConfigurationFileMode,
    ConfigurationMediaType,
    validate_configuration_target_path,
)


ConfigurationScalar: TypeAlias = str | int | float | bool
ConfigurationValue: TypeAlias = (
    ConfigurationScalar
    | tuple["ConfigurationValue", ...]
    | Mapping[str, "ConfigurationValue"]
)

_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_CONTEXT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_SECRET_KEYS = ("password", "secret", "token", "credential", "private_key", "api_key")
_CREDENTIAL_URL = re.compile(r"://[^/@\s]+:[^/@\s]+@")


class ConfigurationParameters(Protocol):
    """A product explicitly exposes only values intended for its template."""

    def configuration_values(self) -> Mapping[str, ConfigurationValue]: ...


class ConfigurationRenderingError(ValueError):
    """Typed product configuration could not become a safe artifact."""


class ConfigurationTemplateSyntaxError(ConfigurationRenderingError):
    """A template definition is not valid Jinja syntax."""

    def __init__(self, template_id: str, *, line: int) -> None:
        self.template_id = template_id
        self.line = line
        super().__init__(
            f"configuration template {template_id!r} is invalid at line {line}"
        )


class ConfigurationTemplateRenderError(ConfigurationRenderingError):
    """A strict template could not render its declared parameters."""

    def __init__(self, template_id: str) -> None:
        self.template_id = template_id
        super().__init__(f"configuration template {template_id!r} could not render")


@dataclass(frozen=True)
class ConfigurationTemplate:
    """One bounded template definition interpreted into one artifact."""

    template_id: str
    artifact_id: str
    target_path: str
    media_type: ConfigurationMediaType
    source: str = field(compare=False, repr=False)
    file_mode: ConfigurationFileMode = ConfigurationFileMode.READ_ONLY
    template_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not _IDENTITY.fullmatch(self.template_id):
            raise ConfigurationRenderingError("configuration template identity is invalid")
        if not _IDENTITY.fullmatch(self.artifact_id):
            raise ConfigurationRenderingError("configuration artifact identity is invalid")
        validate_configuration_target_path(self.target_path)
        if not isinstance(self.media_type, ConfigurationMediaType):
            raise TypeError("configuration template media type must be ConfigurationMediaType")
        if not isinstance(self.file_mode, ConfigurationFileMode):
            raise TypeError("configuration template file mode must be ConfigurationFileMode")
        if not isinstance(self.source, str):
            raise TypeError("configuration template source must be text")
        encoded = self.source.encode("utf-8")
        if not encoded or len(encoded) > MAX_CONFIGURATION_BYTES or "\x00" in self.source:
            raise ConfigurationRenderingError(
                "configuration template is empty or exceeds its bound"
            )
        object.__setattr__(self, "template_digest", hashlib.sha256(encoded).hexdigest())
        _compile_template(self)

    def render(self, parameters: ConfigurationParameters) -> ConfigurationArtifact:
        """Render explicit typed parameters into an immutable artifact."""

        try:
            raw_values = parameters.configuration_values()
        except Exception:
            raise ConfigurationTemplateRenderError(self.template_id) from None
        values = _normalize_context(raw_values)
        template = _compile_template(self)
        chunks: list[str] = []
        size = 0
        try:
            for chunk in template.generate(**values):
                size += len(chunk.encode("utf-8"))
                if size > MAX_CONFIGURATION_BYTES:
                    raise ConfigurationTemplateRenderError(self.template_id)
                chunks.append(chunk)
        except ConfigurationTemplateRenderError:
            raise
        except (TemplateError, TypeError, ValueError, OverflowError):
            raise ConfigurationTemplateRenderError(self.template_id) from None
        content = "".join(chunks)
        source_digest = hashlib.sha256(
            json.dumps(
                {
                    "template_id": self.template_id,
                    "template_digest": self.template_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ConfigurationArtifact(
            self.artifact_id,
            self.target_path,
            self.media_type,
            content,
            self.file_mode,
            source_digest,
        )


def _environment() -> ImmutableSandboxedEnvironment:
    environment = ImmutableSandboxedEnvironment(
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    environment.globals.clear()
    environment.filters.clear()
    environment.filters["json"] = _render_json
    environment.tests.clear()
    return environment


def _compile_template(template: ConfigurationTemplate):
    try:
        return _environment().from_string(template.source)
    except TemplateSyntaxError as error:
        raise ConfigurationTemplateSyntaxError(
            template.template_id,
            line=error.lineno,
        ) from None
    except TemplateError:
        raise ConfigurationTemplateRenderError(template.template_id) from None


def _normalize_context(
    value: Mapping[str, ConfigurationValue],
) -> dict[str, ConfigurationValue]:
    if not isinstance(value, Mapping):
        raise ConfigurationRenderingError("configuration context must be a mapping")
    normalized = {
        key: _normalize_value(item, path=key)
        for key, item in _normalized_items(value)
    }
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ConfigurationRenderingError("configuration context is not bounded data") from None
    if len(encoded) > MAX_CONFIGURATION_BYTES:
        raise ConfigurationRenderingError("configuration context exceeds its bound")
    return normalized


def _validate_context_key(value: object) -> bool:
    if not isinstance(value, str) or not _CONTEXT_KEY.fullmatch(value):
        raise ConfigurationRenderingError("configuration context key is invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_KEYS):
        raise ConfigurationRenderingError("configuration context contains secret-shaped data")
    return True


def _normalize_value(value: object, *, path: str) -> ConfigurationValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationRenderingError("configuration context contains a non-finite number")
        return value
    if isinstance(value, str):
        if (
            "-----BEGIN PRIVATE KEY-----" in value
            or _CREDENTIAL_URL.search(value)
            or len(value.encode("utf-8")) > MAX_CONFIGURATION_BYTES
        ):
            raise ConfigurationRenderingError(
                "configuration context contains secret-shaped or unbounded data"
            )
        return value
    if isinstance(value, tuple):
        return tuple(
            _normalize_value(item, path=f"{path}[]")
            for item in value
        )
    if isinstance(value, Mapping):
        return {
            key: _normalize_value(item, path=f"{path}.{key}")
            for key, item in _normalized_items(value)
        }
    raise ConfigurationRenderingError(
        f"configuration context value at {path!r} is not closed data"
    )


def _render_json(value: ConfigurationValue) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalized_items(value: Mapping[object, object]) -> tuple[tuple[str, object], ...]:
    items: list[tuple[str, object]] = []
    for key, item in value.items():
        _validate_context_key(key)
        items.append((key, item))
    return tuple(sorted(items))
