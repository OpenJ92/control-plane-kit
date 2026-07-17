"""Typed configurable values for runtime/control contracts."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol


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


class ContractMutationError(ValueError):
    """Base error for invalid or conflicting contract mutation intent."""


class StaleContractVersion(ContractMutationError):
    """Raised when mutation intent was authored against another projection."""


class ConflictingContractMutation(ContractMutationError):
    """Raised when one mutation identity is reused for different intent."""


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
class ContractMutation:
    """One version-pinned, idempotently identifiable contract mutation."""

    mutation_id: str
    expected_version: int
    assignments: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mutation_id, str) or not self.mutation_id.strip():
            raise ContractMutationError("contract mutation id must be non-empty text")
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise ContractMutationError(
                "contract mutation expected version must be a non-negative integer"
            )
        if not isinstance(self.assignments, Mapping):
            raise ContractMutationError("contract mutation assignments must be a mapping")
        object.__setattr__(self, "assignments", _immutable_mapping(self.assignments))

    def descriptor(self) -> dict[str, object]:
        """Describe intent without retaining or publishing assigned values."""

        return {
            "mutation_id": self.mutation_id,
            "expected_version": self.expected_version,
            "variables": sorted(self.assignments),
        }


@dataclass(frozen=True)
class ContractCandidate:
    """Immutable, read-only projection prepared from one mutation."""

    mutation_id: str
    base_version: int
    version: int
    changed: Mapping[str, ReloadPolicy]
    _values: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mutation_id, str) or not self.mutation_id.strip():
            raise ContractMutationError("contract candidate mutation id must be non-empty")
        if type(self.base_version) is not int or self.base_version < 0:
            raise ContractMutationError("contract candidate base version is invalid")
        if type(self.version) is not int or self.version < self.base_version:
            raise ContractMutationError("contract candidate version is invalid")
        if self.version > self.base_version + 1:
            raise ContractMutationError(
                "contract candidate may advance at most one version"
            )
        if not all(
            isinstance(name, str) and isinstance(policy, ReloadPolicy)
            for name, policy in self.changed.items()
        ):
            raise ContractMutationError(
                "contract candidate changes must map names to reload policies"
            )
        object.__setattr__(self, "changed", MappingProxyType(dict(self.changed)))
        object.__setattr__(self, "_values", _immutable_mapping(self._values))

    def get(self, name: str) -> Any:
        """Read one candidate value without exposing a mutable projection."""

        if name not in self._values:
            raise KeyError(f"unknown contract variable {name!r}")
        return self._values[name]

    def descriptor(self) -> dict[str, object]:
        """Describe candidate identity and shape without any values."""

        return {
            "mutation_id": self.mutation_id,
            "base_version": self.base_version,
            "version": self.version,
            "changed": {
                name: policy.value for name, policy in sorted(self.changed.items())
            },
        }


@dataclass(frozen=True)
class ContractPatchResult:
    """Result of applying one or more local contract value changes."""

    changed: Mapping[str, ReloadPolicy]
    rebuilt_resources: tuple[str, ...] = ()
    stale_resources: tuple[str, ...] = ()
    mutation_id: str | None = None
    base_version: int = 0
    version: int = 0

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "mutation_id": self.mutation_id,
            "base_version": self.base_version,
            "version": self.version,
            "changed": {
                name: policy.value for name, policy in sorted(self.changed.items())
            },
        }
        if self.rebuilt_resources:
            descriptor["rebuilt_resources"] = sorted(self.rebuilt_resources)
        if self.stale_resources:
            descriptor["stale_resources"] = sorted(self.stale_resources)
        return descriptor


@dataclass(frozen=True)
class DerivedResourceSpec:
    """Contract-owned resource derived from one or more control variables."""

    name: str
    variables: tuple[str, ...]
    build: Callable[["EnvironmentContract"], Any]
    dispose: Callable[[Any], None] | None = None
    rebuild_policies: tuple[ReloadPolicy, ...] = (ReloadPolicy.LIVE, ReloadPolicy.CUSTOM_HANDLER)

    @classmethod
    def from_variables(
        cls,
        name: str,
        variables: str | Iterable[str],
        build: Callable[["EnvironmentContract"], Any],
        *,
        dispose: Callable[[Any], None] | None = None,
        rebuild_policies: Iterable[ReloadPolicy] = (ReloadPolicy.LIVE, ReloadPolicy.CUSTOM_HANDLER),
    ) -> "DerivedResourceSpec":
        if isinstance(variables, str):
            variable_names = (variables,)
        else:
            variable_names = tuple(variables)
        if not variable_names:
            raise ValueError("derived resource must depend on at least one variable")
        return cls(name, variable_names, build, dispose, tuple(rebuild_policies))

    def descriptor(self, *, stale: bool) -> dict[str, object]:
        return {
            "name": self.name,
            "variables": list(self.variables),
            "rebuild_policies": [policy.value for policy in self.rebuild_policies],
            "stale": stale,
        }


class EnvironmentContract:
    """Runtime holder for environment-backed control variables.

    The class body is the declaration. Instances hold validated values. Access
    is always lookup through `get`.
    """

    def __init__(self, values: Mapping[str, Any]):
        declarations = self.declarations()
        self._initialize_state()
        for name, variable in declarations.items():
            raw = values.get(name)
            if raw is None:
                env_name = _env_name(variable)
                if env_name in values:
                    raw = values[env_name]
            self._values[name] = variable.validate(raw)

    def _initialize_state(self) -> None:
        self._values: dict[str, Any] = {}
        self._derived_specs: dict[str, DerivedResourceSpec] = {}
        self._derived_values: dict[str, Any] = {}
        self._stale_derived: set[str] = set()
        self._version = 0
        self._local_mutation_sequence = 0
        self._prepared_mutations: dict[
            str, tuple[ContractMutation, ContractCandidate]
        ] = {}

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

    @property
    def version(self) -> int:
        """Return the current process-local projection version."""

        return self._version

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

    def prepare_mutation(self, mutation: ContractMutation) -> ContractCandidate:
        """Purely prepare one immutable candidate without publishing it."""

        if not isinstance(mutation, ContractMutation):
            raise TypeError("contract mutation preparation requires ContractMutation")
        previous = self._prepared_mutations.get(mutation.mutation_id)
        if previous is not None:
            previous_mutation, candidate = previous
            if previous_mutation == mutation:
                return candidate
            raise ConflictingContractMutation(
                f"contract mutation id {mutation.mutation_id!r} has conflicting intent"
            )
        if mutation.expected_version != self._version:
            raise StaleContractVersion(
                f"contract mutation expected version {mutation.expected_version}, "
                f"current version is {self._version}"
            )

        validated = self.validate_patch(mutation.assignments)
        declarations = self.declarations()
        values = dict(self._values)
        changed: dict[str, ReloadPolicy] = {}
        for name, value in validated.items():
            if values.get(name) != value:
                values[name] = value
                changed[name] = declarations[name].reload_policy
        candidate = ContractCandidate(
            mutation_id=mutation.mutation_id,
            base_version=self._version,
            version=self._version + (1 if changed else 0),
            changed=changed,
            _values=values,
        )
        self._prepared_mutations[mutation.mutation_id] = (mutation, candidate)
        return candidate

    def apply_patch(self, patch: Mapping[str, Any]) -> ContractPatchResult:
        self._local_mutation_sequence += 1
        return self.apply_mutation(
            ContractMutation(
                mutation_id=f"local-{self._local_mutation_sequence}",
                expected_version=self._version,
                assignments=patch,
            )
        )

    def apply_mutation(self, mutation: ContractMutation) -> ContractPatchResult:
        """Publish a candidate through the legacy resource path.

        Issue #281 replaces this publication section with all-resource
        preparation and one atomic projection swap.
        """

        candidate = self.prepare_mutation(mutation)
        self._values = dict(candidate._values)
        self._version = candidate.version
        self._prepared_mutations.pop(candidate.mutation_id, None)
        rebuilt, stale = self._apply_derived_resource_policy(candidate.changed)
        return ContractPatchResult(
            candidate.changed,
            tuple(rebuilt),
            tuple(stale),
            mutation_id=candidate.mutation_id,
            base_version=candidate.base_version,
            version=candidate.version,
        )

    def derived(
        self,
        name: str,
        from_var: str | Iterable[str],
        build: Callable[["EnvironmentContract"], Any],
        *,
        dispose: Callable[[Any], None] | None = None,
        rebuild_policies: Iterable[ReloadPolicy] = (ReloadPolicy.LIVE, ReloadPolicy.CUSTOM_HANDLER),
    ) -> Any:
        spec = DerivedResourceSpec.from_variables(
            name,
            from_var,
            build,
            dispose=dispose,
            rebuild_policies=rebuild_policies,
        )
        self._validate_derived_spec(spec)
        self._derived_specs[name] = spec
        self._derived_values[name] = spec.build(self)
        self._stale_derived.discard(name)
        return self._derived_values[name]

    def get_derived(self, name: str) -> Any:
        if name not in self._derived_values:
            raise KeyError(f"unknown derived resource {name!r}")
        return self._derived_values[name]

    def is_derived_stale(self, name: str) -> bool:
        if name not in self._derived_specs:
            raise KeyError(f"unknown derived resource {name!r}")
        return name in self._stale_derived

    def rebuild_derived(self, name: str) -> Any:
        if name not in self._derived_specs:
            raise KeyError(f"unknown derived resource {name!r}")
        spec = self._derived_specs[name]
        old_value = self._derived_values.get(name)
        new_value = spec.build(self)
        self._derived_values[name] = new_value
        self._stale_derived.discard(name)
        if old_value is not None and spec.dispose is not None:
            spec.dispose(old_value)
        return new_value

    def _validate_derived_spec(self, spec: DerivedResourceSpec) -> None:
        declarations = self.declarations()
        for variable in spec.variables:
            if variable not in declarations:
                raise KeyError(f"unknown contract variable {variable!r}")

    def _apply_derived_resource_policy(self, changed: Mapping[str, ReloadPolicy]) -> tuple[list[str], list[str]]:
        rebuilt: list[str] = []
        stale: list[str] = []
        changed_names = set(changed)
        if not changed_names:
            return rebuilt, stale
        for name, spec in self._derived_specs.items():
            touched = changed_names.intersection(spec.variables)
            if not touched:
                continue
            policies = {changed[variable] for variable in touched}
            if policies.issubset(set(spec.rebuild_policies)):
                self.rebuild_derived(name)
                rebuilt.append(name)
            else:
                self._stale_derived.add(name)
                stale.append(name)
        return rebuilt, stale

    def descriptor(self) -> dict[str, object]:
        return self.redacted_descriptor()

    def redacted_descriptor(self) -> dict[str, object]:
        return {
            "version": self._version,
            "variables": {
                name: _redacted_variable_descriptor(variable, self._values.get(name))
                for name, variable in sorted(self.declarations().items())
            },
            "derived_resources": self._derived_descriptor(),
        }

    def unsafe_descriptor(self) -> dict[str, object]:
        return {
            "version": self._version,
            "variables": {
                name: variable.descriptor(self._values.get(name), include_value=True)
                for name, variable in sorted(self.declarations().items())
            },
            "derived_resources": self._derived_descriptor(),
            "unsafe": True,
        }

    def _derived_descriptor(self) -> dict[str, object]:
        return {
            name: spec.descriptor(stale=name in self._stale_derived)
            for name, spec in sorted(self._derived_specs.items())
        }


class RuntimeContract(EnvironmentContract):
    """Runtime holder for mutable process state that is not env-backed.

    The class body is the declaration. Instances hold validated runtime values.
    Access is always lookup through `get`. Unlike `EnvironmentContract`, this
    holder never falls back to `os.environ`.
    """

    def __init__(self, values: Mapping[str, Any]):
        declarations = self.declarations()
        self._initialize_state()
        for name, variable in declarations.items():
            self._values[name] = variable.validate(values.get(name))

    @classmethod
    def from_process(cls) -> "RuntimeContract":
        raise TypeError("RuntimeContract values must be supplied explicitly")

    def descriptor(self) -> dict[str, object]:
        descriptor = self.redacted_descriptor()
        descriptor["runtime"] = True
        return descriptor

    def unsafe_descriptor(self) -> dict[str, object]:
        descriptor = super().unsafe_descriptor()
        descriptor["runtime"] = True
        return descriptor


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


def _immutable_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy and recursively freeze a mapping at an algebra boundary."""

    return MappingProxyType(
        {
            key: _immutable_value(value)
            for key, value in values.items()
        }
    )


def _immutable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _immutable_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_immutable_value(item) for item in value)
    return deepcopy(value)
