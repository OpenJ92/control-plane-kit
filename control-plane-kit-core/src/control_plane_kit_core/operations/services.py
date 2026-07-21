"""Pure service-role boundary for a composed control-plane program."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class InvalidDeploymentProgramBoundary(ValueError):
    """Raised when the control-plane service composition is incoherent."""


class ControlPlaneServiceRole(StrEnum):
    """Closed generic roles required by a control-plane application program."""

    PLANNING = "planning"
    APPROVAL = "approval"
    ADMISSION = "admission"
    LIFECYCLE = "lifecycle"
    EXECUTION = "execution"
    RECOVERY = "recovery"
    OBSERVATION = "observation"
    READS = "reads"
    AUTHORIZATION = "authorization"


class DeploymentProgramStage(StrEnum):
    """Closed public stage names for a graph transition program."""

    PLAN = "plan"
    APPROVE = "approve"
    ADMIT = "admit"
    CLAIM = "claim"
    EXECUTE = "execute"
    ADVANCE = "advance"


_STAGE_ROLES = {
    DeploymentProgramStage.PLAN: ControlPlaneServiceRole.PLANNING,
    DeploymentProgramStage.APPROVE: ControlPlaneServiceRole.APPROVAL,
    DeploymentProgramStage.ADMIT: ControlPlaneServiceRole.ADMISSION,
    DeploymentProgramStage.CLAIM: ControlPlaneServiceRole.LIFECYCLE,
    DeploymentProgramStage.EXECUTE: ControlPlaneServiceRole.EXECUTION,
    DeploymentProgramStage.ADVANCE: ControlPlaneServiceRole.LIFECYCLE,
}

_FORBIDDEN_PROCESS_TERMS = (
    "cpi",
    "cpk-server",
    "dockerfile",
    "fastapi",
    "mcp-server",
    "oci-image",
    "product-descriptor",
    "uvicorn",
)


@dataclass(frozen=True)
class ApplicationServiceBinding:
    """One generic service role bound into a deployment program composition."""

    role: ControlPlaneServiceRole
    service_name: str
    parameters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.role, ControlPlaneServiceRole):
            raise InvalidDeploymentProgramBoundary(
                "service binding role must be ControlPlaneServiceRole"
            )
        if not isinstance(self.service_name, str) or not self.service_name.strip():
            raise InvalidDeploymentProgramBoundary("service_name must be non-empty text")
        if not isinstance(self.parameters, tuple) or not all(
            isinstance(parameter, str) and parameter.strip()
            for parameter in self.parameters
        ):
            raise InvalidDeploymentProgramBoundary("parameters must be non-empty text")
        _reject_process_terms(self.service_name)
        for parameter in self.parameters:
            _reject_process_terms(parameter)

    def descriptor(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "service_name": self.service_name,
            "parameters": list(self.parameters),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ApplicationServiceBinding":
        if set(value) != {"role", "service_name", "parameters"}:
            raise InvalidDeploymentProgramBoundary(
                "service binding descriptor has unexpected keys"
            )
        parameters = value["parameters"]
        if not isinstance(parameters, list):
            raise InvalidDeploymentProgramBoundary("parameters must be a list")
        try:
            return cls(
                role=ControlPlaneServiceRole(_text(value["role"], "role")),
                service_name=_text(value["service_name"], "service_name"),
                parameters=tuple(_text(parameter, "parameter") for parameter in parameters),
            )
        except ValueError as error:
            raise InvalidDeploymentProgramBoundary(str(error)) from error


@dataclass(frozen=True)
class DeploymentProgramBoundary:
    """Pure boundary describing the services a DeploymentProgram composes."""

    services: tuple[ApplicationServiceBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.services, tuple) or not all(
            isinstance(service, ApplicationServiceBinding)
            for service in self.services
        ):
            raise InvalidDeploymentProgramBoundary(
                "services must be ApplicationServiceBinding values"
            )

        by_role = {service.role: service for service in self.services}
        if len(by_role) != len(self.services):
            raise InvalidDeploymentProgramBoundary("service roles must be unique")

        required = set(ControlPlaneServiceRole)
        actual = set(by_role)
        if actual != required:
            missing = ", ".join(
                role.value for role in ControlPlaneServiceRole if role not in actual
            )
            extra = ", ".join(sorted(role.value for role in actual - required))
            details = []
            if missing:
                details.append(f"missing: {missing}")
            if extra:
                details.append(f"extra: {extra}")
            raise InvalidDeploymentProgramBoundary(
                "deployment program boundary must bind every service role"
                + (f" ({'; '.join(details)})" if details else "")
            )

        ordered = tuple(by_role[role] for role in ControlPlaneServiceRole)
        object.__setattr__(self, "services", ordered)

    def service(self, role: ControlPlaneServiceRole) -> ApplicationServiceBinding:
        if not isinstance(role, ControlPlaneServiceRole):
            raise InvalidDeploymentProgramBoundary("role must be ControlPlaneServiceRole")
        return self.services[tuple(ControlPlaneServiceRole).index(role)]

    def descriptor(self) -> dict[str, object]:
        return {
            "services": [service.descriptor() for service in self.services],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "DeploymentProgramBoundary":
        if set(value) != {"services"}:
            raise InvalidDeploymentProgramBoundary(
                "deployment program descriptor has unexpected keys"
            )
        services = value["services"]
        if not isinstance(services, list):
            raise InvalidDeploymentProgramBoundary("services must be a list")
        return cls(
            tuple(
                ApplicationServiceBinding.from_descriptor(
                    _mapping(service, "service")
                )
                for service in services
            )
        )


@dataclass(frozen=True)
class DeploymentStageContract:
    """One public operation stage without an implementation callback."""

    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    requires_prior_stage: DeploymentProgramStage | None
    creates_durable_handoff: bool

    def __post_init__(self) -> None:
        if not isinstance(self.stage, DeploymentProgramStage):
            raise InvalidDeploymentProgramBoundary(
                "stage must be DeploymentProgramStage"
            )
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidDeploymentProgramBoundary(
                "service_role must be ControlPlaneServiceRole"
            )
        expected_role = _STAGE_ROLES[self.stage]
        if self.service_role is not expected_role:
            raise InvalidDeploymentProgramBoundary(
                f"{self.stage.value} must use {expected_role.value} service"
            )
        if (
            self.requires_prior_stage is not None
            and not isinstance(self.requires_prior_stage, DeploymentProgramStage)
        ):
            raise InvalidDeploymentProgramBoundary(
                "requires_prior_stage must be DeploymentProgramStage"
            )
        if type(self.creates_durable_handoff) is not bool:
            raise InvalidDeploymentProgramBoundary(
                "creates_durable_handoff must be bool"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "service_role": self.service_role.value,
            "requires_prior_stage": (
                None
                if self.requires_prior_stage is None
                else self.requires_prior_stage.value
            ),
            "creates_durable_handoff": self.creates_durable_handoff,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "DeploymentStageContract":
        if set(value) != {
            "stage",
            "service_role",
            "requires_prior_stage",
            "creates_durable_handoff",
        }:
            raise InvalidDeploymentProgramBoundary(
                "deployment stage descriptor has unexpected keys"
            )
        prior = value["requires_prior_stage"]
        try:
            return cls(
                stage=DeploymentProgramStage(_text(value["stage"], "stage")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                requires_prior_stage=(
                    None
                    if prior is None
                    else DeploymentProgramStage(
                        _text(prior, "requires_prior_stage")
                    )
                ),
                creates_durable_handoff=_bool(
                    value["creates_durable_handoff"],
                    "creates_durable_handoff",
                ),
            )
        except ValueError as error:
            raise InvalidDeploymentProgramBoundary(str(error)) from error


@dataclass(frozen=True)
class DeploymentStagePipeline:
    """Pure public stage sequence for deploying one graph transition."""

    stages: tuple[DeploymentStageContract, ...] = ()

    def __post_init__(self) -> None:
        stages = self.stages or canonical_deployment_stage_pipeline().stages
        if not isinstance(stages, tuple) or not all(
            isinstance(stage, DeploymentStageContract)
            for stage in stages
        ):
            raise InvalidDeploymentProgramBoundary(
                "stages must be DeploymentStageContract values"
            )
        expected = tuple(DeploymentProgramStage)
        actual = tuple(stage.stage for stage in stages)
        if actual != expected:
            raise InvalidDeploymentProgramBoundary(
                "deployment stages must be canonical plan/approve/admit/claim/execute/advance"
            )
        prior_by_stage = {
            stage.stage: stage.requires_prior_stage
            for stage in stages
        }
        for index, stage in enumerate(expected):
            expected_prior = None if index == 0 else expected[index - 1]
            if prior_by_stage[stage] is not expected_prior:
                raise InvalidDeploymentProgramBoundary(
                    f"{stage.value} has incoherent predecessor"
                )
        object.__setattr__(self, "stages", stages)

    def descriptor(self) -> dict[str, object]:
        return {
            "stages": [stage.descriptor() for stage in self.stages],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "DeploymentStagePipeline":
        if set(value) != {"stages"}:
            raise InvalidDeploymentProgramBoundary(
                "deployment stage pipeline descriptor has unexpected keys"
            )
        stages = value["stages"]
        if not isinstance(stages, list):
            raise InvalidDeploymentProgramBoundary("stages must be a list")
        return cls(
            tuple(
                DeploymentStageContract.from_descriptor(
                    _mapping(stage, "stage")
                )
                for stage in stages
            )
        )


def canonical_deployment_stage_pipeline() -> DeploymentStagePipeline:
    """Return the public stage law for a graph transition program."""

    stages = tuple(DeploymentProgramStage)
    return DeploymentStagePipeline(
        tuple(
            DeploymentStageContract(
                stage=stage,
                service_role=_STAGE_ROLES[stage],
                requires_prior_stage=None if index == 0 else stages[index - 1],
                creates_durable_handoff=True,
            )
            for index, stage in enumerate(stages)
        )
    )


def _reject_process_terms(value: str) -> None:
    normalized = value.casefold().replace("_", "-")
    for term in _FORBIDDEN_PROCESS_TERMS:
        if term in normalized:
            raise InvalidDeploymentProgramBoundary(
                f"{value!r} names process packaging rather than a generic service"
            )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidDeploymentProgramBoundary(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidDeploymentProgramBoundary(f"{field} must be bool")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidDeploymentProgramBoundary(f"{field} must be a descriptor")
    return value
