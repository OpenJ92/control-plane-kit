"""Typed configurable values for runtime/control contracts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Protocol


class ReloadPolicy(StrEnum):
    """How a running node must react when a control value changes."""

    LIVE = "live"
    RESTART_REQUIRED = "restart-required"
    DRAIN_REQUIRED = "drain-required"
    IMMUTABLE = "immutable"
    CUSTOM_HANDLER = "custom-handler"


class ControlValueKind(StrEnum):
    """Primitive control value families understood by descriptors."""

    TEXT = "text"
    HTTP = "http"
    TCP = "tcp"
    POSTGRES = "postgres"
    SECRET = "secret"
    RUNTIME_VALUE = "runtime-value"
    RUNTIME_MAP = "runtime-map"


@dataclass(frozen=True)
class ValidationErrorDetail:
    """Structured validation failure for one control variable."""

    variable: str
    code: str
    message: str

    def descriptor(self) -> dict[str, str]:
        return {"variable": self.variable, "code": self.code, "message": self.message}


class ControlVariableError(ValueError):
    """Raised when a control variable cannot validate or accept a value."""

    def __init__(self, detail: ValidationErrorDetail):
        super().__init__(detail.message)
        self.detail = detail


class ControlVariable(Protocol):
    """Inspectable declaration for a configurable value."""

    name: str
    kind: ControlValueKind
    mutable: bool
    required: bool
    reload_policy: ReloadPolicy

    def validate(self, value: Any) -> Any:
        """Validate and normalize a value."""

    def descriptor(self, value: Any = None, *, include_value: bool = False) -> Mapping[str, object]:
        """Describe this variable without exposing unsafe values by default."""


@dataclass(frozen=True)
class ControlVariableSpec:
    """Base data object for control variable declarations."""

    name: str
    kind: ControlValueKind
    mutable: bool = True
    required: bool = True
    reload_policy: ReloadPolicy = ReloadPolicy.LIVE
    description: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def validate(self, value: Any) -> Any:
        if value is None:
            if self.required:
                raise self._error("required", f"{self.name} is required")
            return None
        return value

    def descriptor(self, value: Any = None, *, include_value: bool = False) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": self.name,
            "kind": self.kind.value,
            "mutable": self.mutable,
            "required": self.required,
            "reload_policy": self.reload_policy.value,
            "metadata": dict(self.metadata),
        }
        if self.description is not None:
            descriptor["description"] = self.description
        if include_value:
            descriptor["value"] = self.describe_value(value)
        return descriptor

    def describe_value(self, value: Any) -> object:
        return value

    def _error(self, code: str, message: str) -> ControlVariableError:
        return ControlVariableError(ValidationErrorDetail(self.name, code, message))


@dataclass(frozen=True)
class TextVariable(ControlVariableSpec):
    """Plain text control variable."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = True,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.TEXT, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> str | None:
        value = super().validate(value)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._error("type", f"{self.name} must be text")
        return value


@dataclass(frozen=True)
class HttpVariable(ControlVariableSpec):
    """HTTP/HTTPS URL control variable."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = True,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.HTTP, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> str | None:
        value = _require_text(self, super().validate(value))
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise self._error("url-scheme", f"{self.name} must start with http:// or https://")
        return value


@dataclass(frozen=True)
class TcpVariable(ControlVariableSpec):
    """TCP endpoint control variable."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = True,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.TCP, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> str | None:
        value = _require_text(self, super().validate(value))
        if value is None:
            return None
        if ":" not in value:
            raise self._error("tcp-address", f"{self.name} must include host:port")
        host, port = value.rsplit(":", 1)
        if not host or not port.isdigit():
            raise self._error("tcp-address", f"{self.name} must include host:port")
        return value


@dataclass(frozen=True)
class PostgresVariable(ControlVariableSpec):
    """Postgres connection-string control variable."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = True,
        reload_policy: ReloadPolicy = ReloadPolicy.DRAIN_REQUIRED,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.POSTGRES, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> str | None:
        value = _require_text(self, super().validate(value))
        if value is None:
            return None
        if not value.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise self._error("postgres-url", f"{self.name} must be a Postgres connection string")
        return value


@dataclass(frozen=True)
class SecretVariable(ControlVariableSpec):
    """Secret control variable that never describes raw values."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = True,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.SECRET, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> str | None:
        return _require_text(self, super().validate(value))

    def describe_value(self, value: Any) -> dict[str, object]:
        return {"present": value is not None and value != "", "redacted": True}


@dataclass(frozen=True)
class RuntimeValueVariable(ControlVariableSpec):
    """Runtime-only value that is not backed by an environment variable."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = False,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.RUNTIME_VALUE, mutable, required, reload_policy, description, metadata or {})


@dataclass(frozen=True)
class RuntimeMapVariable(ControlVariableSpec):
    """Runtime-only mapping value."""

    def __init__(
        self,
        name: str,
        *,
        mutable: bool = True,
        required: bool = False,
        reload_policy: ReloadPolicy = ReloadPolicy.LIVE,
        description: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ):
        super().__init__(name, ControlValueKind.RUNTIME_MAP, mutable, required, reload_policy, description, metadata or {})

    def validate(self, value: Any) -> Mapping[str, object] | None:
        value = super().validate(value)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise self._error("type", f"{self.name} must be a mapping")
        return dict(value)


def _require_text(variable: ControlVariableSpec, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise variable._error("type", f"{variable.name} must be text")
    return value


@dataclass(frozen=True)
class ContractPatchResult:
    """Result of applying one or more local contract value changes."""

    changed: Mapping[str, ReloadPolicy]

    def descriptor(self) -> dict[str, str]:
        return {name: policy.value for name, policy in sorted(self.changed.items())}


class EnvironmentContract:
    """Runtime holder for environment-backed control variables.

    The class body is the declaration. Instances hold validated values. Access
    is always lookup through `get`.
    """

    def __init__(self, values: Mapping[str, Any]):
        declarations = self.declarations()
        self._values: dict[str, Any] = {}
        for name, variable in declarations.items():
            raw = values.get(name)
            if raw is None:
                env_name = _env_name(variable)
                if env_name in values:
                    raw = values[env_name]
            self._values[name] = variable.validate(raw)

    @classmethod
    def declarations(cls) -> dict[str, ControlVariableSpec]:
        declarations: dict[str, ControlVariableSpec] = {}
        for base in reversed(cls.__mro__):
            for name, value in vars(base).items():
                if isinstance(value, ControlVariableSpec):
                    declarations[name] = _named_variable(name, value)
        return declarations

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "EnvironmentContract":
        return cls(values)

    @classmethod
    def from_process(cls) -> "EnvironmentContract":
        return cls.from_mapping(os.environ)

    def get(self, name: str) -> Any:
        if name not in self._values:
            raise KeyError(f"unknown contract variable {name!r}")
        return self._values[name]

    def set(self, name: str, value: Any) -> ContractPatchResult:
        return self.apply_patch({name: value})

    def validate_patch(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        declarations = self.declarations()
        validated: dict[str, Any] = {}
        for name, value in patch.items():
            if name not in declarations:
                raise KeyError(f"unknown contract variable {name!r}")
            variable = declarations[name]
            if not variable.mutable or variable.reload_policy is ReloadPolicy.IMMUTABLE:
                raise variable._error("immutable", f"{name} is immutable")
            validated[name] = variable.validate(value)
        return validated

    def apply_patch(self, patch: Mapping[str, Any]) -> ContractPatchResult:
        validated = self.validate_patch(patch)
        declarations = self.declarations()
        changed: dict[str, ReloadPolicy] = {}
        for name, value in validated.items():
            if self._values.get(name) != value:
                self._values[name] = value
                changed[name] = declarations[name].reload_policy
        return ContractPatchResult(changed)

    def descriptor(self) -> dict[str, object]:
        return self.redacted_descriptor()

    def redacted_descriptor(self) -> dict[str, object]:
        return {
            "variables": {
                name: _redacted_variable_descriptor(variable, self._values.get(name))
                for name, variable in sorted(self.declarations().items())
            }
        }

    def unsafe_descriptor(self) -> dict[str, object]:
        return {
            "variables": {
                name: variable.descriptor(self._values.get(name), include_value=True)
                for name, variable in sorted(self.declarations().items())
            },
            "unsafe": True,
        }


def _named_variable(name: str, variable: ControlVariableSpec) -> ControlVariableSpec:
    if variable.name == name:
        return variable
    return replace(variable, name=name)


def _env_name(variable: ControlVariableSpec) -> str:
    return variable.metadata.get("env", variable.name.upper())


def _redacted_variable_descriptor(variable: ControlVariableSpec, value: Any) -> dict[str, object]:
    descriptor = variable.descriptor(value, include_value=False)
    descriptor["value"] = variable.describe_value(value) if isinstance(variable, SecretVariable) else {"present": value is not None, "redacted": True}
    return descriptor
