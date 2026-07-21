"""Pure UnitOfWork and external-effect boundary language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit_core.operations.services import (
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
)


class InvalidUnitOfWorkBoundary(ValueError):
    """Raised when service transaction participation is incoherent."""


class StoreParticipation(StrEnum):
    """Closed vocabulary for how a service participates in durable stores."""

    NONE = "none"
    READ_ONLY = "read-only"
    READ_WRITE = "read-write"


class ExternalEffectPolicy(StrEnum):
    """Closed vocabulary for where external effects may occur."""

    FORBIDDEN = "forbidden"
    AFTER_COMMIT = "after-commit"
    INSIDE_TRANSACTION = "inside-transaction"


@dataclass(frozen=True)
class ServiceTransactionBoundary:
    """Transaction and authority law for one control-plane service role."""

    role: ControlPlaneServiceRole
    store_participation: StoreParticipation
    owns_transaction: bool = False
    external_effect_policy: ExternalEffectPolicy = ExternalEffectPolicy.FORBIDDEN
    uses_worker: bool = False
    uses_runtime_authority: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.role, ControlPlaneServiceRole):
            raise InvalidUnitOfWorkBoundary("role must be ControlPlaneServiceRole")
        if not isinstance(self.store_participation, StoreParticipation):
            raise InvalidUnitOfWorkBoundary(
                "store_participation must be StoreParticipation"
            )
        if not isinstance(self.external_effect_policy, ExternalEffectPolicy):
            raise InvalidUnitOfWorkBoundary(
                "external_effect_policy must be ExternalEffectPolicy"
            )
        if not isinstance(self.owns_transaction, bool):
            raise InvalidUnitOfWorkBoundary("owns_transaction must be bool")
        if not isinstance(self.uses_worker, bool):
            raise InvalidUnitOfWorkBoundary("uses_worker must be bool")
        if not isinstance(self.uses_runtime_authority, bool):
            raise InvalidUnitOfWorkBoundary("uses_runtime_authority must be bool")

        if (
            self.store_participation is StoreParticipation.READ_WRITE
            and not self.owns_transaction
        ):
            raise InvalidUnitOfWorkBoundary(
                "read-write services must own the operator-command transaction"
            )
        if self.external_effect_policy is ExternalEffectPolicy.INSIDE_TRANSACTION:
            raise InvalidUnitOfWorkBoundary(
                "external effects must not run inside a transaction"
            )
        if (
            self.external_effect_policy is ExternalEffectPolicy.AFTER_COMMIT
            and not self.owns_transaction
        ):
            raise InvalidUnitOfWorkBoundary(
                "after-commit effects require an operator-command transaction"
            )
        if self.uses_runtime_authority and (
            self.external_effect_policy is not ExternalEffectPolicy.AFTER_COMMIT
        ):
            raise InvalidUnitOfWorkBoundary(
                "runtime authority may only be used for after-commit effects"
            )
        if self.role in {
            ControlPlaneServiceRole.READS,
            ControlPlaneServiceRole.AUTHORIZATION,
        } and (self.uses_worker or self.uses_runtime_authority):
            raise InvalidUnitOfWorkBoundary(
                "read and authorization services must not use workers or runtime authority"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "store_participation": self.store_participation.value,
            "owns_transaction": self.owns_transaction,
            "external_effect_policy": self.external_effect_policy.value,
            "uses_worker": self.uses_worker,
            "uses_runtime_authority": self.uses_runtime_authority,
        }


@dataclass(frozen=True)
class UnitOfWorkBoundary:
    """Pure descriptor for one-command transaction ownership across services."""

    program: DeploymentProgramBoundary
    services: tuple[ServiceTransactionBoundary, ...]
    transaction_boundary: str = "operator-command"
    store_commit_policy: str = "stores-never-commit"

    def __post_init__(self) -> None:
        if not isinstance(self.program, DeploymentProgramBoundary):
            raise InvalidUnitOfWorkBoundary(
                "program must be a DeploymentProgramBoundary"
            )
        if not isinstance(self.services, tuple) or not all(
            isinstance(service, ServiceTransactionBoundary)
            for service in self.services
        ):
            raise InvalidUnitOfWorkBoundary(
                "services must be ServiceTransactionBoundary values"
            )

        by_role = {service.role: service for service in self.services}
        if len(by_role) != len(self.services):
            raise InvalidUnitOfWorkBoundary("transaction service roles must be unique")

        program_roles = {service.role for service in self.program.services}
        actual_roles = set(by_role)
        required_roles = set(ControlPlaneServiceRole)
        if actual_roles != required_roles or actual_roles != program_roles:
            missing = ", ".join(
                role.value for role in ControlPlaneServiceRole if role not in actual_roles
            )
            extra = ", ".join(
                role.value for role in ControlPlaneServiceRole if role in actual_roles - required_roles
            )
            details = []
            if missing:
                details.append(f"missing: {missing}")
            if extra:
                details.append(f"extra: {extra}")
            raise InvalidUnitOfWorkBoundary(
                "unit of work boundary must name every program service role"
                + (f" ({'; '.join(details)})" if details else "")
            )

        ordered = tuple(by_role[role] for role in ControlPlaneServiceRole)
        object.__setattr__(self, "services", ordered)

    def service(self, role: ControlPlaneServiceRole) -> ServiceTransactionBoundary:
        if not isinstance(role, ControlPlaneServiceRole):
            raise InvalidUnitOfWorkBoundary("role must be ControlPlaneServiceRole")
        return self.services[tuple(ControlPlaneServiceRole).index(role)]

    def descriptor(self) -> dict[str, object]:
        return {
            "transaction_boundary": self.transaction_boundary,
            "store_commit_policy": self.store_commit_policy,
            "services": [service.descriptor() for service in self.services],
        }
