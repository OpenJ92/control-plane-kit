"""Pure handoff contracts for the future cpk-server process package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.parity import (
    AdapterCommandParityContract,
    AdapterOperationSecurityParityContract,
    AdapterParityContract,
)
from control_plane_kit_core.operations.process import ControlPlaneProcessContract
from control_plane_kit_core.operations.services import DeploymentProgramBoundary
from control_plane_kit_core.operations.transactions import UnitOfWorkBoundary


class InvalidCpkServerHandoffContract(ValueError):
    """Raised when the cpk-server handoff contract is incoherent."""


class EntrypointCompositionPolicy(StrEnum):
    """Closed composition policy for the future cpk-server wrapper."""

    ONE_DEPLOYMENT_PROGRAM = "one-deployment-program"


class ProcessStatePolicy(StrEnum):
    """Closed process state policy for hosted control-plane adapters."""

    PROCESS_GLOBALS_ARE_NOT_TRUTH = "process-globals-are-not-truth"
    PROCESS_GLOBALS_OWN_TRUTH = "process-globals-own-truth"


@dataclass(frozen=True)
class CpkServerEntrypointHandoffContract:
    """Core contract the external cpk-server process package must satisfy."""

    process: ControlPlaneProcessContract
    program: DeploymentProgramBoundary
    unit_of_work: UnitOfWorkBoundary
    projection_parity: AdapterParityContract
    command_parity: AdapterCommandParityContract
    security_parity: AdapterOperationSecurityParityContract
    implementation_package: str = "control-plane-kit-servers/cpk-server"
    import_direction: str = "cpk-server-imports-core"
    composition_policy: EntrypointCompositionPolicy = (
        EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM
    )
    state_policy: ProcessStatePolicy = (
        ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH
    )

    def __post_init__(self) -> None:
        if not isinstance(self.process, ControlPlaneProcessContract):
            raise InvalidCpkServerHandoffContract(
                "process must be ControlPlaneProcessContract"
            )
        if not isinstance(self.program, DeploymentProgramBoundary):
            raise InvalidCpkServerHandoffContract(
                "program must be DeploymentProgramBoundary"
            )
        if not isinstance(self.unit_of_work, UnitOfWorkBoundary):
            raise InvalidCpkServerHandoffContract(
                "unit_of_work must be UnitOfWorkBoundary"
            )
        if not isinstance(self.projection_parity, AdapterParityContract):
            raise InvalidCpkServerHandoffContract(
                "projection_parity must be AdapterParityContract"
            )
        if not isinstance(self.command_parity, AdapterCommandParityContract):
            raise InvalidCpkServerHandoffContract(
                "command_parity must be AdapterCommandParityContract"
            )
        if not isinstance(
            self.security_parity,
            AdapterOperationSecurityParityContract,
        ):
            raise InvalidCpkServerHandoffContract(
                "security_parity must be AdapterOperationSecurityParityContract"
            )
        if self.implementation_package != "control-plane-kit-servers/cpk-server":
            raise InvalidCpkServerHandoffContract(
                "cpk-server implementation belongs to control-plane-kit-servers/cpk-server"
            )
        if self.import_direction != "cpk-server-imports-core":
            raise InvalidCpkServerHandoffContract(
                "cpk-server must import core; core must not import cpk-server"
            )
        if self.composition_policy is not (
            EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM
        ):
            raise InvalidCpkServerHandoffContract(
                "cpk-server must compose one DeploymentProgram"
            )
        if self.state_policy is not ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH:
            raise InvalidCpkServerHandoffContract(
                "process globals must not own workflow truth"
            )
        if self.process.http_api is None:
            raise InvalidCpkServerHandoffContract(
                "cpk-server process handoff requires HTTP API contract"
            )
        if self.process.mcp is None:
            raise InvalidCpkServerHandoffContract(
                "cpk-server process handoff requires MCP contract"
            )
        if self.unit_of_work.program != self.program:
            raise InvalidCpkServerHandoffContract(
                "UnitOfWorkBoundary must describe the handoff program"
            )
        if self.process.http_api != self.projection_parity.http_api:
            raise InvalidCpkServerHandoffContract(
                "process HTTP API must match projection parity"
            )
        if self.process.http_api != self.command_parity.http_api:
            raise InvalidCpkServerHandoffContract(
                "process HTTP API must match command parity"
            )
        if self.process.mcp != self.projection_parity.mcp:
            raise InvalidCpkServerHandoffContract(
                "process MCP contract must match projection parity"
            )
        if self.process.mcp != self.command_parity.mcp:
            raise InvalidCpkServerHandoffContract(
                "process MCP contract must match command parity"
            )
        if self.command_parity.unit_of_work != self.unit_of_work:
            raise InvalidCpkServerHandoffContract(
                "command parity must use the handoff UnitOfWorkBoundary"
            )
        if self.security_parity.projection_parity != self.projection_parity:
            raise InvalidCpkServerHandoffContract(
                "security parity must use the handoff projection parity"
            )
        if self.security_parity.command_parity != self.command_parity:
            raise InvalidCpkServerHandoffContract(
                "security parity must use the handoff command parity"
            )

    @property
    def http_api(self):
        return self.process.http_api

    @property
    def mcp(self):
        return self.process.mcp

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "cpk-server-entrypoint-handoff",
            "implementation_package": self.implementation_package,
            "import_direction": self.import_direction,
            "composition_policy": self.composition_policy.value,
            "state_policy": self.state_policy.value,
            "process": self.process.descriptor(),
            "program": self.program.descriptor(),
            "unit_of_work": self.unit_of_work.descriptor(),
            "projection_parity": self.projection_parity.descriptor(),
            "command_parity": self.command_parity.descriptor(),
            "security_parity": self.security_parity.descriptor(),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "CpkServerEntrypointHandoffContract":
        if set(value) != {
            "kind",
            "implementation_package",
            "import_direction",
            "composition_policy",
            "state_policy",
            "process",
            "program",
            "unit_of_work",
            "projection_parity",
            "command_parity",
            "security_parity",
        }:
            raise InvalidCpkServerHandoffContract(
                "cpk-server handoff descriptor has unexpected keys"
            )
        if value["kind"] != "cpk-server-entrypoint-handoff":
            raise InvalidCpkServerHandoffContract(
                "cpk-server handoff descriptor has wrong kind"
            )
        try:
            return cls(
                implementation_package=_text(
                    value["implementation_package"],
                    "implementation_package",
                ),
                import_direction=_text(value["import_direction"], "import_direction"),
                composition_policy=EntrypointCompositionPolicy(
                    _text(value["composition_policy"], "composition_policy")
                ),
                state_policy=ProcessStatePolicy(
                    _text(value["state_policy"], "state_policy")
                ),
                process=ControlPlaneProcessContract.from_descriptor(
                    _mapping(value["process"], "process")
                ),
                program=DeploymentProgramBoundary.from_descriptor(
                    _mapping(value["program"], "program")
                ),
                unit_of_work=UnitOfWorkBoundary.from_descriptor(
                    _mapping(value["unit_of_work"], "unit_of_work")
                ),
                projection_parity=AdapterParityContract.from_descriptor(
                    _mapping(value["projection_parity"], "projection_parity")
                ),
                command_parity=AdapterCommandParityContract.from_descriptor(
                    _mapping(value["command_parity"], "command_parity")
                ),
                security_parity=(
                    AdapterOperationSecurityParityContract.from_descriptor(
                        _mapping(value["security_parity"], "security_parity")
                    )
                ),
            )
        except ValueError as error:
            raise InvalidCpkServerHandoffContract(str(error)) from error


def canonical_cpk_server_entrypoint_handoff(
    *,
    process: ControlPlaneProcessContract,
    program: DeploymentProgramBoundary,
    unit_of_work: UnitOfWorkBoundary,
    projection_parity: AdapterParityContract,
    command_parity: AdapterCommandParityContract,
    security_parity: AdapterOperationSecurityParityContract,
) -> CpkServerEntrypointHandoffContract:
    """Construct the canonical core-to-cpk-server entrypoint handoff."""

    return CpkServerEntrypointHandoffContract(
        process=process,
        program=program,
        unit_of_work=unit_of_work,
        projection_parity=projection_parity,
        command_parity=command_parity,
        security_parity=security_parity,
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidCpkServerHandoffContract(f"{field} must be text")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidCpkServerHandoffContract(f"{field} must be a descriptor")
    return value
