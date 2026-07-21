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


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidDeploymentProgramBoundary(f"{field} must be a descriptor")
    return value
