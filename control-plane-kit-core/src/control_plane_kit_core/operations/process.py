"""Pure process-operation handoff contracts for cpk-server."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.http import HttpApiContract
from control_plane_kit_core.operations.mcp import McpStreamableHttpContract
from control_plane_kit_core.verification import (
    VerificationContract,
    VerificationContractError,
)


class InvalidProcessOperationalContract(ValueError):
    """Raised when a process operational contract is incoherent."""


class ProcessEndpointKind(StrEnum):
    """Closed health endpoint semantics."""

    LIVENESS = "liveness"
    READINESS = "readiness"


class DependencyReadinessKind(StrEnum):
    """Closed dependency kinds that can make readiness fail."""

    STORE = "store"
    RUNTIME_AUTHORITY = "runtime-authority"
    WORKER = "worker"
    HTTP_API = "http-api"
    MCP_STREAMABLE_HTTP = "mcp-streamable-http"
    OBSERVATION = "observation"


@dataclass(frozen=True)
class HttpStatusProbeContract:
    """Bounded status probe for liveness or readiness."""

    kind: ProcessEndpointKind
    path: str
    public: bool
    reveals_sensitive_state: bool = False
    expected_statuses: tuple[int, ...] = (200,)
    maximum_response_bytes: int = 1024

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProcessEndpointKind):
            raise InvalidProcessOperationalContract(
                "probe kind must be ProcessEndpointKind"
            )
        _validate_path(self.path, "probe path")
        if type(self.public) is not bool:
            raise InvalidProcessOperationalContract("probe public must be bool")
        if type(self.reveals_sensitive_state) is not bool:
            raise InvalidProcessOperationalContract(
                "probe reveals_sensitive_state must be bool"
            )
        if not isinstance(self.expected_statuses, tuple) or not all(
            type(status) is int and 100 <= status <= 599
            for status in self.expected_statuses
        ):
            raise InvalidProcessOperationalContract(
                "probe statuses must be integer HTTP statuses"
            )
        if tuple(sorted(set(self.expected_statuses))) != self.expected_statuses:
            raise InvalidProcessOperationalContract(
                "probe statuses must be unique and sorted"
            )
        if (
            type(self.maximum_response_bytes) is not int
            or not 1 <= self.maximum_response_bytes <= 65536
        ):
            raise InvalidProcessOperationalContract(
                "probe maximum_response_bytes must be between 1 and 65536"
            )
        if self.kind is ProcessEndpointKind.LIVENESS:
            if not self.public:
                raise InvalidProcessOperationalContract("liveness must be public")
            if self.reveals_sensitive_state:
                raise InvalidProcessOperationalContract(
                    "public liveness must not reveal sensitive state"
                )
        if self.kind is ProcessEndpointKind.READINESS and self.public:
            raise InvalidProcessOperationalContract("readiness must not be public")

    @classmethod
    def liveness(cls) -> "HttpStatusProbeContract":
        return cls(ProcessEndpointKind.LIVENESS, "/health/live", public=True)

    @classmethod
    def readiness(cls) -> "HttpStatusProbeContract":
        return cls(ProcessEndpointKind.READINESS, "/health/ready", public=False)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "public": self.public,
            "reveals_sensitive_state": self.reveals_sensitive_state,
            "expected_statuses": list(self.expected_statuses),
            "maximum_response_bytes": self.maximum_response_bytes,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "HttpStatusProbeContract":
        if set(value) != {
            "kind",
            "path",
            "public",
            "reveals_sensitive_state",
            "expected_statuses",
            "maximum_response_bytes",
        }:
            raise InvalidProcessOperationalContract(
                "probe descriptor has unexpected keys"
            )
        statuses = value["expected_statuses"]
        if not isinstance(statuses, list):
            raise InvalidProcessOperationalContract(
                "probe expected_statuses must be a list"
            )
        try:
            return cls(
                kind=ProcessEndpointKind(_text(value["kind"], "probe kind")),
                path=_text(value["path"], "probe path"),
                public=_bool(value["public"], "probe public"),
                reveals_sensitive_state=_bool(
                    value["reveals_sensitive_state"],
                    "probe reveals_sensitive_state",
                ),
                expected_statuses=tuple(statuses),
                maximum_response_bytes=_integer(
                    value["maximum_response_bytes"],
                    "probe maximum_response_bytes",
                ),
            )
        except ValueError as error:
            raise InvalidProcessOperationalContract(str(error)) from error


@dataclass(frozen=True)
class ReadinessDependency:
    """One dependency whose absence makes readiness fail."""

    kind: DependencyReadinessKind
    evidence_key: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DependencyReadinessKind):
            raise InvalidProcessOperationalContract(
                "dependency kind must be DependencyReadinessKind"
            )
        if type(self.required) is not bool:
            raise InvalidProcessOperationalContract("dependency required must be bool")
        evidence_key = self.evidence_key or self.kind.value
        _validate_identity(evidence_key, "dependency evidence_key")
        _reject_sensitive_text(evidence_key, "dependency evidence_key")
        object.__setattr__(self, "evidence_key", evidence_key)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "evidence_key": self.evidence_key,
            "required": self.required,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "ReadinessDependency":
        if set(value) != {"kind", "evidence_key", "required"}:
            raise InvalidProcessOperationalContract(
                "dependency descriptor has unexpected keys"
            )
        try:
            return cls(
                kind=DependencyReadinessKind(_text(value["kind"], "dependency kind")),
                evidence_key=_text(
                    value["evidence_key"],
                    "dependency evidence_key",
                ),
                required=_bool(value["required"], "dependency required"),
            )
        except ValueError as error:
            raise InvalidProcessOperationalContract(str(error)) from error


@dataclass(frozen=True)
class ObservationHandoffContract:
    """How process observations relate to graph truth."""

    projection: str = "append-only"
    graph_truth_policy: str = "never-rewrite-desired-graph"
    maximum_evidence_bytes: int = 16384

    def __post_init__(self) -> None:
        if self.projection != "append-only":
            raise InvalidProcessOperationalContract(
                "observation projection must be append-only"
            )
        if self.graph_truth_policy != "never-rewrite-desired-graph":
            raise InvalidProcessOperationalContract(
                "observations must never rewrite desired graph truth"
            )
        if (
            type(self.maximum_evidence_bytes) is not int
            or not 1 <= self.maximum_evidence_bytes <= 65536
        ):
            raise InvalidProcessOperationalContract(
                "observation evidence bound must be between 1 and 65536"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "projection": self.projection,
            "graph_truth_policy": self.graph_truth_policy,
            "maximum_evidence_bytes": self.maximum_evidence_bytes,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ObservationHandoffContract":
        if set(value) != {
            "projection",
            "graph_truth_policy",
            "maximum_evidence_bytes",
        }:
            raise InvalidProcessOperationalContract(
                "observation descriptor has unexpected keys"
            )
        return cls(
            projection=_text(value["projection"], "observation projection"),
            graph_truth_policy=_text(
                value["graph_truth_policy"],
                "observation graph_truth_policy",
            ),
            maximum_evidence_bytes=_integer(
                value["maximum_evidence_bytes"],
                "observation maximum_evidence_bytes",
            ),
        )


@dataclass(frozen=True)
class ShutdownContract:
    """Shutdown handoff contract for retained-data preservation."""

    graceful_timeout_seconds: float = 30.0
    retained_data_policy: str = "preserve-retained-data"
    records_observation: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.graceful_timeout_seconds, (int, float))
            or isinstance(self.graceful_timeout_seconds, bool)
            or not 0 < self.graceful_timeout_seconds <= 300
        ):
            raise InvalidProcessOperationalContract(
                "shutdown timeout must be greater than zero and at most 300 seconds"
            )
        object.__setattr__(
            self,
            "graceful_timeout_seconds",
            float(self.graceful_timeout_seconds),
        )
        if self.retained_data_policy != "preserve-retained-data":
            raise InvalidProcessOperationalContract(
                "shutdown must preserve retained data"
            )
        if self.records_observation is not True:
            raise InvalidProcessOperationalContract(
                "shutdown must record an observation"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "graceful_timeout_seconds": self.graceful_timeout_seconds,
            "retained_data_policy": self.retained_data_policy,
            "records_observation": self.records_observation,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "ShutdownContract":
        if set(value) != {
            "graceful_timeout_seconds",
            "retained_data_policy",
            "records_observation",
        }:
            raise InvalidProcessOperationalContract(
                "shutdown descriptor has unexpected keys"
            )
        return cls(
            graceful_timeout_seconds=_number(
                value["graceful_timeout_seconds"],
                "shutdown graceful_timeout_seconds",
            ),
            retained_data_policy=_text(
                value["retained_data_policy"],
                "shutdown retained_data_policy",
            ),
            records_observation=_bool(
                value["records_observation"],
                "shutdown records_observation",
            ),
        )


@dataclass(frozen=True)
class ControlPlaneProcessContract:
    """Core handoff contract for the future hosted control-plane process."""

    liveness: HttpStatusProbeContract = field(
        default_factory=HttpStatusProbeContract.liveness
    )
    readiness: HttpStatusProbeContract = field(
        default_factory=HttpStatusProbeContract.readiness
    )
    dependencies: tuple[ReadinessDependency, ...] = ()
    verification: VerificationContract = field(default_factory=VerificationContract)
    observation: ObservationHandoffContract = field(
        default_factory=ObservationHandoffContract
    )
    shutdown: ShutdownContract = field(default_factory=ShutdownContract)
    http_api: HttpApiContract | None = None
    mcp: McpStreamableHttpContract | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.liveness, HttpStatusProbeContract):
            raise InvalidProcessOperationalContract(
                "liveness must be HttpStatusProbeContract"
            )
        if self.liveness.kind is not ProcessEndpointKind.LIVENESS:
            raise InvalidProcessOperationalContract("liveness probe has wrong kind")
        if not isinstance(self.readiness, HttpStatusProbeContract):
            raise InvalidProcessOperationalContract(
                "readiness must be HttpStatusProbeContract"
            )
        if self.readiness.kind is not ProcessEndpointKind.READINESS:
            raise InvalidProcessOperationalContract("readiness probe has wrong kind")
        if self.liveness.path == self.readiness.path:
            raise InvalidProcessOperationalContract(
                "liveness and readiness paths must be distinct"
            )
        if not isinstance(self.dependencies, tuple) or not all(
            isinstance(dependency, ReadinessDependency)
            for dependency in self.dependencies
        ):
            raise InvalidProcessOperationalContract(
                "dependencies must be ReadinessDependency values"
            )
        by_kind = {dependency.kind: dependency for dependency in self.dependencies}
        if len(by_kind) != len(self.dependencies):
            raise InvalidProcessOperationalContract(
                "dependency kinds must be unique"
            )
        if not isinstance(self.verification, VerificationContract):
            raise InvalidProcessOperationalContract(
                "verification must be VerificationContract"
            )
        if not isinstance(self.observation, ObservationHandoffContract):
            raise InvalidProcessOperationalContract(
                "observation must be ObservationHandoffContract"
            )
        if not isinstance(self.shutdown, ShutdownContract):
            raise InvalidProcessOperationalContract("shutdown must be ShutdownContract")
        if (
            DependencyReadinessKind.HTTP_API in by_kind
            and not isinstance(self.http_api, HttpApiContract)
        ):
            raise InvalidProcessOperationalContract(
                "HTTP API readiness requires an HttpApiContract"
            )
        if (
            DependencyReadinessKind.MCP_STREAMABLE_HTTP in by_kind
            and not isinstance(self.mcp, McpStreamableHttpContract)
        ):
            raise InvalidProcessOperationalContract(
                "MCP readiness requires an McpStreamableHttpContract"
            )
        ordered = tuple(
            by_kind[kind]
            for kind in DependencyReadinessKind
            if kind in by_kind
        )
        object.__setattr__(self, "dependencies", ordered)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "control-plane-process-contract",
            "liveness": self.liveness.descriptor(),
            "readiness": self.readiness.descriptor(),
            "dependencies": [
                dependency.descriptor() for dependency in self.dependencies
            ],
            "verification": self.verification.descriptor(),
            "observation": self.observation.descriptor(),
            "shutdown": self.shutdown.descriptor(),
            "http_api": None if self.http_api is None else self.http_api.descriptor(),
            "mcp": None if self.mcp is None else self.mcp.descriptor(),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ControlPlaneProcessContract":
        if set(value) != {
            "kind",
            "liveness",
            "readiness",
            "dependencies",
            "verification",
            "observation",
            "shutdown",
            "http_api",
            "mcp",
        }:
            raise InvalidProcessOperationalContract(
                "process contract descriptor has unexpected keys"
            )
        if value["kind"] != "control-plane-process-contract":
            raise InvalidProcessOperationalContract(
                "process contract descriptor has wrong kind"
            )
        liveness = _mapping(value["liveness"], "liveness")
        readiness = _mapping(value["readiness"], "readiness")
        dependencies = value["dependencies"]
        if not isinstance(dependencies, list):
            raise InvalidProcessOperationalContract("dependencies must be a list")
        try:
            return cls(
                liveness=HttpStatusProbeContract.from_descriptor(liveness),
                readiness=HttpStatusProbeContract.from_descriptor(readiness),
                dependencies=tuple(
                    ReadinessDependency.from_descriptor(
                        _mapping(dependency, "dependency")
                    )
                    for dependency in dependencies
                ),
                verification=VerificationContract.from_descriptor(
                    value["verification"]
                ),
                observation=ObservationHandoffContract.from_descriptor(
                    _mapping(value["observation"], "observation")
                ),
                shutdown=ShutdownContract.from_descriptor(
                    _mapping(value["shutdown"], "shutdown")
                ),
                http_api=_optional_http_api(value["http_api"]),
                mcp=_optional_mcp(value["mcp"]),
            )
        except (TypeError, ValueError, VerificationContractError) as error:
            raise InvalidProcessOperationalContract(str(error)) from error


def _optional_http_api(value: object) -> HttpApiContract | None:
    if value is None:
        return None
    return HttpApiContract.from_descriptor(_mapping(value, "http_api"))


def _optional_mcp(value: object) -> McpStreamableHttpContract | None:
    if value is None:
        return None
    return McpStreamableHttpContract.from_descriptor(_mapping(value, "mcp"))


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidProcessOperationalContract(f"{field} must be a descriptor")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidProcessOperationalContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidProcessOperationalContract(f"{field} must be bool")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise InvalidProcessOperationalContract(f"{field} must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InvalidProcessOperationalContract(f"{field} must be a number")
    return float(value)


def _validate_path(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise InvalidProcessOperationalContract(f"{field} must be absolute")
    if "?" in value or "#" in value or any(character.isspace() for character in value):
        raise InvalidProcessOperationalContract(
            f"{field} must not include query, fragment, or whitespace"
        )


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidProcessOperationalContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidProcessOperationalContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _reject_sensitive_text(value: str, field: str) -> None:
    normalized = value.casefold()
    for term in ("secret", "token", "password"):
        if term in normalized:
            raise InvalidProcessOperationalContract(
                f"{field} must not name sensitive state"
            )
