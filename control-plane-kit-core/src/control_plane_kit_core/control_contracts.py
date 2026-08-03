"""Pure control-contract value declarations and redacted snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from control_plane_kit_core.types import Protocol


_MAX_VALUE_BYTES = 16_384
_MAX_METADATA_FIELDS = 16
_MAX_METADATA_VALUE_BYTES = 512


class ControlValueKind(StrEnum):
    """Closed value shapes exposed to operator control contracts."""

    TEXT = "text"
    HTTP = "http"
    TCP = "tcp"
    POSTGRES = "postgres"
    SECRET = "secret"
    RUNTIME_VALUE = "runtime-value"
    RUNTIME_MAP = "runtime-map"


class ReloadPolicy(StrEnum):
    """Pure reload intent attached to one control variable declaration."""

    RESTART = "restart"
    LIVE = "live"
    DRAIN = "drain"
    NONE = "none"


class ControlContractCode(StrEnum):
    """Closed validation codes for control-contract diagnostics."""

    REQUIRED = "required"
    INVALID = "invalid"
    IMMUTABLE = "immutable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, order=True)
class ControlContractDiagnostic:
    """Bounded structural validation failure with no supplied value."""

    variable: str
    code: ControlContractCode
    message: str

    def descriptor(self) -> dict[str, str]:
        return {
            "variable": self.variable,
            "code": self.code.value,
            "message": self.message,
        }


class ControlContractError(ValueError):
    """Raised when a pure control-contract value violates its declaration."""

    def __init__(self, detail: ControlContractDiagnostic) -> None:
        self.detail = detail
        super().__init__(detail.message)


@dataclass(frozen=True, order=True)
class ControlVariableSpec:
    """One immutable control variable declaration."""

    name: str
    kind: ControlValueKind
    mutable: bool = True
    required: bool = True
    reload_policy: ReloadPolicy = ReloadPolicy.RESTART
    description: str | None = None
    metadata: Mapping[str, str] | tuple[tuple[str, str], ...] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("control variable name is malformed")
        if not isinstance(self.kind, ControlValueKind):
            raise TypeError("control variable kind must be ControlValueKind")
        if type(self.mutable) is not bool:
            raise TypeError("control variable mutable flag must be bool")
        if type(self.required) is not bool:
            raise TypeError("control variable required flag must be bool")
        if not isinstance(self.reload_policy, ReloadPolicy):
            raise TypeError("control variable reload_policy must be ReloadPolicy")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("control variable description must be a string")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def validate(self, value: object) -> object:
        """Return the validated value or raise a bounded structural error."""

        if value is None:
            if self.required:
                raise self._error(ControlContractCode.REQUIRED, f"{self.name} is required")
            return None
        if self.kind is ControlValueKind.TEXT:
            return self._validate_text(value)
        if self.kind is ControlValueKind.HTTP:
            return self._validate_url(value, Protocol.HTTP)
        if self.kind is ControlValueKind.POSTGRES:
            return self._validate_url(value, Protocol.POSTGRES)
        if self.kind is ControlValueKind.TCP:
            return self._validate_tcp(value)
        if self.kind is ControlValueKind.SECRET:
            return self._validate_text(value)
        if self.kind is ControlValueKind.RUNTIME_VALUE:
            return self._validate_text(value)
        if self.kind is ControlValueKind.RUNTIME_MAP:
            return self._validate_runtime_map(value)
        raise AssertionError(f"unhandled control value kind {self.kind!r}")

    def descriptor(
        self,
        value: object = None,
        *,
        include_value: bool = False,
        unsafe: bool = False,
        redact_value: bool = False,
    ) -> dict[str, object]:
        """Return a deterministic descriptor, redacting values unless explicitly unsafe."""

        descriptor: dict[str, object] = {
            "name": self.name,
            "kind": self.kind.value,
            "mutable": self.mutable,
            "required": self.required,
            "reload_policy": self.reload_policy.value,
            "metadata": dict(self.metadata),
            "description": self.description,
        }
        if include_value:
            descriptor["value"] = self._descriptor_value(
                value,
                unsafe=unsafe,
                redact_value=redact_value,
            )
        return descriptor

    def _descriptor_value(
        self,
        value: object,
        *,
        unsafe: bool,
        redact_value: bool,
    ) -> object:
        if self.kind is ControlValueKind.SECRET or redact_value:
            return {"present": value is not None, "redacted": True}
        return self.validate(value)

    def _validate_text(self, value: object) -> str:
        if not isinstance(value, str):
            raise self._error(ControlContractCode.INVALID, f"{self.name} must be a string")
        if "\x00" in value or len(value.encode("utf-8")) > _MAX_VALUE_BYTES:
            raise self._error(ControlContractCode.INVALID, f"{self.name} is malformed")
        return value

    def _validate_url(self, value: object, protocol: Protocol) -> str:
        text = self._validate_text(value)
        try:
            parsed = urlsplit(text)
        except ValueError as error:
            raise self._error(ControlContractCode.INVALID, f"{self.name} is malformed") from error
        if parsed.scheme not in protocol.endpoint_schemes() or not parsed.netloc:
            raise self._error(
                ControlContractCode.INVALID,
                f"{self.name} must use {protocol.value}",
            )
        return text

    def _validate_tcp(self, value: object) -> str:
        text = self._validate_text(value)
        if "://" in text or ":" not in text:
            raise self._error(ControlContractCode.INVALID, f"{self.name} must be host:port")
        host, port = text.rsplit(":", 1)
        if not host or not port.isdecimal():
            raise self._error(ControlContractCode.INVALID, f"{self.name} must be host:port")
        numeric_port = int(port)
        if numeric_port < 1 or numeric_port > 65_535:
            raise self._error(ControlContractCode.INVALID, f"{self.name} must be host:port")
        return text

    def _validate_runtime_map(self, value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise self._error(ControlContractCode.INVALID, f"{self.name} must be a mapping")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise self._error(ControlContractCode.INVALID, f"{self.name} keys must be strings")
            if not isinstance(item, str):
                raise self._error(ControlContractCode.INVALID, f"{self.name} values must be strings")
            self._validate_text(item)
            result[key] = item
        return dict(sorted(result.items()))

    def _error(self, code: ControlContractCode, message: str) -> ControlContractError:
        return ControlContractError(ControlContractDiagnostic(self.name, code, message))


@dataclass(frozen=True)
class ControlContract:
    """A pure declaration of control values accepted for one boundary."""

    variables: tuple[ControlVariableSpec, ...]
    runtime: bool = False

    def __post_init__(self) -> None:
        variables = tuple(self.variables)
        for variable in variables:
            if not isinstance(variable, ControlVariableSpec):
                raise TypeError("control contract variables must be ControlVariableSpec")
        names = tuple(variable.name for variable in variables)
        if len(set(names)) != len(names):
            raise ValueError("control contract variable names must be unique")
        if type(self.runtime) is not bool:
            raise TypeError("control contract runtime flag must be bool")
        object.__setattr__(self, "variables", variables)

    def load(self, values: Mapping[str, object]) -> "ControlContractSnapshot":
        """Validate explicit supplied values without reading process state."""

        if not isinstance(values, Mapping):
            raise TypeError("control contract values must be a mapping")
        validated: dict[str, object] = {}
        for variable in self.variables:
            validated[variable.name] = variable.validate(
                _lookup_value(values, variable)
            )
        return ControlContractSnapshot(self, validated)

    def load_from_process(self) -> "ControlContractSnapshot":
        """Deliberately unsupported in core; callers must supply state explicitly."""

        raise TypeError("control-plane-kit-core does not read process environment")


@dataclass(frozen=True)
class ControlContractSnapshot:
    """An immutable validated view of supplied control values."""

    contract: ControlContract
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.contract, ControlContract):
            raise TypeError("snapshot contract must be ControlContract")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def get(self, name: str) -> object:
        return self.values[name]

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(unsafe=False)

    def unsafe_descriptor(self) -> dict[str, object]:
        descriptor = self._descriptor(unsafe=True)
        descriptor["unsafe"] = True
        return descriptor

    def prepare_patch(self, values: Mapping[str, object]) -> dict[str, object]:
        """Validate a candidate change without publishing mutable holder state."""

        if not isinstance(values, Mapping):
            raise TypeError("control contract patch must be a mapping")
        variable_by_name = {variable.name: variable for variable in self.contract.variables}
        changed: dict[str, object] = {}
        for name, value in values.items():
            variable = variable_by_name.get(name)
            if variable is None:
                raise ControlContractError(
                    ControlContractDiagnostic(
                        str(name),
                        ControlContractCode.UNKNOWN,
                        f"{name} is not declared",
                    )
                )
            if not variable.mutable:
                raise variable._error(
                    ControlContractCode.IMMUTABLE,
                    f"{variable.name} is immutable",
                )
            changed[name] = variable.validate(value)
        return changed

    def _descriptor(self, *, unsafe: bool) -> dict[str, object]:
        return {
            "runtime": self.contract.runtime,
            "variables": {
                variable.name: variable.descriptor(
                    self.values[variable.name],
                    include_value=True,
                    unsafe=unsafe,
                    redact_value=not unsafe,
                )
                for variable in self.contract.variables
            },
        }


def _lookup_value(values: Mapping[str, object], variable: ControlVariableSpec) -> object:
    if variable.name in values:
        return values[variable.name]
    env_name = variable.metadata.get("env")
    if env_name is not None and env_name in values:
        return values[env_name]
    return None


def _metadata(value: Mapping[str, str] | tuple[tuple[str, str], ...]) -> MappingProxyType[str, str]:
    if not isinstance(value, Mapping):
        value = dict(value)
    if len(value) > _MAX_METADATA_FIELDS:
        raise ValueError("control variable metadata has too many fields")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("control variable metadata keys must be strings")
        if not isinstance(item, str):
            raise ValueError("control variable metadata values must be strings")
        if len(item.encode("utf-8")) > _MAX_METADATA_VALUE_BYTES:
            raise ValueError("control variable metadata value is too large")
        result[key] = item
    return MappingProxyType(dict(sorted(result.items())))
