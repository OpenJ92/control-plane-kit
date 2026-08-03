"""Pure persistence, UnitOfWork, and mutation-holder handoff contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.lifecycle import ContractEnforcementOwner
from control_plane_kit_core.operations.services import ControlPlaneServiceRole
from control_plane_kit_core.operations.transactions import StoreParticipation


class InvalidPersistenceBoundaryContract(ValueError):
    """Raised when persistence boundary contract data is incoherent."""


class DurableStoreKind(StrEnum):
    """Closed durable store roles without repository implementation."""

    WORKSPACE = "workspace"
    GRAPH_TOPOLOGY = "graph-topology"
    ACTIVITY_HISTORY = "activity-history"
    OBSERVED_STATE = "observed-state"
    SECRET_REFERENCE = "secret-reference"
    INSTANCE_REGISTRY = "instance-registry"
    EXECUTION_JOURNAL = "execution-journal"
    OPERATION_LEDGER = "operation-ledger"


class StoreOrderingPolicy(StrEnum):
    """Closed consistency policy names for durable store handoff."""

    VERSIONED_POINTER = "versioned-pointer"
    APPEND_ONLY_ORDINAL = "append-only-ordinal"
    LATEST_BY_TIME_THEN_ID = "latest-by-time-then-id"
    UNIQUE_SCOPED_IDEMPOTENCY = "unique-scoped-idempotency"
    REFERENCE_ONLY = "reference-only"


class MutationSubjectKind(StrEnum):
    """Closed mutable contract subjects that operations may interpret."""

    ENVIRONMENT_CONTRACT = "environment-contract"
    RUNTIME_CONTRACT = "runtime-contract"
    DERIVED_RESOURCE = "derived-resource"


class MutationPhaseKind(StrEnum):
    """Closed mutation phases without mutable holder code."""

    PREPARE_CANDIDATE = "prepare-candidate"
    VALIDATE_ASSIGNMENTS = "validate-assignments"
    PUBLISH = "publish"
    REPLAY_IDENTITY = "replay-identity"
    MARK_STALE = "mark-stale"
    REBUILD = "rebuild"
    CLEANUP_SUPERSEDED = "cleanup-superseded"
    PRESERVE_RETAINED = "preserve-retained"


class PersistenceHandoffKind(StrEnum):
    """Closed operations handoff categories for persistence machinery."""

    UNIT_OF_WORK = "unit-of-work"
    RELATIONAL_SCHEMA = "relational-schema"
    STORE_REPOSITORY = "store-repository"
    MUTATION_HOLDER = "mutation-holder"
    CLEANUP_EXECUTOR = "cleanup-executor"


class FailureVisibilityPolicy(StrEnum):
    """Closed visibility policy for rollback, cleanup, and uncertainty."""

    ROLLBACK_ALL_PARTICIPANTS = "rollback-all-participants"
    BOUNDED_EVIDENCE_NO_VALUES = "bounded-evidence-no-values"
    PRESERVE_PRIOR_PROJECTION = "preserve-prior-projection"
    OPERATOR_VISIBLE_UNCERTAINTY = "operator-visible-uncertainty"


_STORE_SERVICE_ROLES = {
    DurableStoreKind.WORKSPACE: ControlPlaneServiceRole.LIFECYCLE,
    DurableStoreKind.GRAPH_TOPOLOGY: ControlPlaneServiceRole.PLANNING,
    DurableStoreKind.ACTIVITY_HISTORY: ControlPlaneServiceRole.PLANNING,
    DurableStoreKind.OBSERVED_STATE: ControlPlaneServiceRole.OBSERVATION,
    DurableStoreKind.SECRET_REFERENCE: ControlPlaneServiceRole.AUTHORIZATION,
    DurableStoreKind.INSTANCE_REGISTRY: ControlPlaneServiceRole.AUTHORIZATION,
    DurableStoreKind.EXECUTION_JOURNAL: ControlPlaneServiceRole.EXECUTION,
    DurableStoreKind.OPERATION_LEDGER: ControlPlaneServiceRole.PLANNING,
}

_STORE_ORDERING = {
    DurableStoreKind.WORKSPACE: StoreOrderingPolicy.VERSIONED_POINTER,
    DurableStoreKind.GRAPH_TOPOLOGY: StoreOrderingPolicy.VERSIONED_POINTER,
    DurableStoreKind.ACTIVITY_HISTORY: StoreOrderingPolicy.APPEND_ONLY_ORDINAL,
    DurableStoreKind.OBSERVED_STATE: StoreOrderingPolicy.LATEST_BY_TIME_THEN_ID,
    DurableStoreKind.SECRET_REFERENCE: StoreOrderingPolicy.REFERENCE_ONLY,
    DurableStoreKind.INSTANCE_REGISTRY: StoreOrderingPolicy.VERSIONED_POINTER,
    DurableStoreKind.EXECUTION_JOURNAL: StoreOrderingPolicy.APPEND_ONLY_ORDINAL,
    DurableStoreKind.OPERATION_LEDGER: StoreOrderingPolicy.UNIQUE_SCOPED_IDEMPOTENCY,
}


@dataclass(frozen=True)
class DurableStoreContract:
    """One store role contract without schema or repository code."""

    store: DurableStoreKind
    service_role: ControlPlaneServiceRole
    participation: StoreParticipation
    ordering_policy: StoreOrderingPolicy
    accepts_secret_values: bool
    stores_never_commit: bool
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        if not isinstance(self.store, DurableStoreKind):
            raise InvalidPersistenceBoundaryContract(
                "store must be DurableStoreKind"
            )
        if self.service_role is not _STORE_SERVICE_ROLES[self.store]:
            raise InvalidPersistenceBoundaryContract(
                f"{self.store.value} has wrong service role"
            )
        if not isinstance(self.participation, StoreParticipation):
            raise InvalidPersistenceBoundaryContract(
                "participation must be StoreParticipation"
            )
        if self.participation is not StoreParticipation.READ_WRITE:
            raise InvalidPersistenceBoundaryContract("durable stores are read-write")
        if self.ordering_policy is not _STORE_ORDERING[self.store]:
            raise InvalidPersistenceBoundaryContract(
                f"{self.store.value} has wrong ordering policy"
            )
        _validate_bool(self.accepts_secret_values, "accepts_secret_values")
        _validate_bool(self.stores_never_commit, "stores_never_commit")
        if self.accepts_secret_values:
            raise InvalidPersistenceBoundaryContract(
                "stores must not accept secret values"
            )
        if not self.stores_never_commit:
            raise InvalidPersistenceBoundaryContract("stores never commit")
        _validate_owner(self.enforcement_owner, "store enforcement")

    def descriptor(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "service_role": self.service_role.value,
            "participation": self.participation.value,
            "ordering_policy": self.ordering_policy.value,
            "accepts_secret_values": self.accepts_secret_values,
            "stores_never_commit": self.stores_never_commit,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "DurableStoreContract":
        if set(value) != {
            "store",
            "service_role",
            "participation",
            "ordering_policy",
            "accepts_secret_values",
            "stores_never_commit",
            "enforcement_owner",
        }:
            raise InvalidPersistenceBoundaryContract(
                "store descriptor has unexpected keys"
            )
        try:
            return cls(
                store=DurableStoreKind(_text(value["store"], "store")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                participation=StoreParticipation(
                    _text(value["participation"], "participation")
                ),
                ordering_policy=StoreOrderingPolicy(
                    _text(value["ordering_policy"], "ordering_policy")
                ),
                accepts_secret_values=_bool(
                    value["accepts_secret_values"],
                    "accepts_secret_values",
                ),
                stores_never_commit=_bool(
                    value["stores_never_commit"],
                    "stores_never_commit",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidPersistenceBoundaryContract(str(error)) from error


@dataclass(frozen=True)
class MutationHolderContract:
    """One mutable-holder phase contract without holder implementation."""

    subject: MutationSubjectKind
    phase: MutationPhaseKind
    requires_candidate: bool
    publishes_values: bool
    failure_visibility: FailureVisibilityPolicy
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        if not isinstance(self.subject, MutationSubjectKind):
            raise InvalidPersistenceBoundaryContract(
                "subject must be MutationSubjectKind"
            )
        if not isinstance(self.phase, MutationPhaseKind):
            raise InvalidPersistenceBoundaryContract(
                "phase must be MutationPhaseKind"
            )
        _validate_bool(self.requires_candidate, "requires_candidate")
        _validate_bool(self.publishes_values, "publishes_values")
        if self.publishes_values:
            raise InvalidPersistenceBoundaryContract(
                "mutation descriptors must not publish values"
            )
        if not isinstance(self.failure_visibility, FailureVisibilityPolicy):
            raise InvalidPersistenceBoundaryContract(
                "failure_visibility must be FailureVisibilityPolicy"
            )
        if (
            self.phase
            in {
                MutationPhaseKind.PUBLISH,
                MutationPhaseKind.REPLAY_IDENTITY,
                MutationPhaseKind.CLEANUP_SUPERSEDED,
            }
            and not self.requires_candidate
        ):
            raise InvalidPersistenceBoundaryContract(
                f"{self.phase.value} requires candidate identity"
            )
        _validate_owner(self.enforcement_owner, "mutation enforcement")

    def descriptor(self) -> dict[str, object]:
        return {
            "subject": self.subject.value,
            "phase": self.phase.value,
            "requires_candidate": self.requires_candidate,
            "publishes_values": self.publishes_values,
            "failure_visibility": self.failure_visibility.value,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "MutationHolderContract":
        if set(value) != {
            "subject",
            "phase",
            "requires_candidate",
            "publishes_values",
            "failure_visibility",
            "enforcement_owner",
        }:
            raise InvalidPersistenceBoundaryContract(
                "mutation descriptor has unexpected keys"
            )
        try:
            return cls(
                subject=MutationSubjectKind(_text(value["subject"], "subject")),
                phase=MutationPhaseKind(_text(value["phase"], "phase")),
                requires_candidate=_bool(
                    value["requires_candidate"],
                    "requires_candidate",
                ),
                publishes_values=_bool(value["publishes_values"], "publishes_values"),
                failure_visibility=FailureVisibilityPolicy(
                    _text(value["failure_visibility"], "failure_visibility")
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidPersistenceBoundaryContract(str(error)) from error


@dataclass(frozen=True)
class PersistenceHandoffContract:
    """One operations handoff boundary for persistence machinery."""

    kind: PersistenceHandoffKind
    requires_unit_of_work: bool
    requires_caller_owned_transaction: bool
    allows_core_database_driver: bool
    allows_core_schema_ddl: bool
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PersistenceHandoffKind):
            raise InvalidPersistenceBoundaryContract(
                "kind must be PersistenceHandoffKind"
            )
        _validate_bool(self.requires_unit_of_work, "requires_unit_of_work")
        _validate_bool(
            self.requires_caller_owned_transaction,
            "requires_caller_owned_transaction",
        )
        _validate_bool(
            self.allows_core_database_driver,
            "allows_core_database_driver",
        )
        _validate_bool(self.allows_core_schema_ddl, "allows_core_schema_ddl")
        if self.allows_core_database_driver:
            raise InvalidPersistenceBoundaryContract(
                "core must not import database drivers"
            )
        if self.allows_core_schema_ddl:
            raise InvalidPersistenceBoundaryContract(
                "core must not own schema DDL"
            )
        if self.requires_unit_of_work and not self.requires_caller_owned_transaction:
            raise InvalidPersistenceBoundaryContract(
                "UnitOfWork handoff requires caller-owned transaction"
            )
        _validate_owner(self.enforcement_owner, "persistence handoff")

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "requires_unit_of_work": self.requires_unit_of_work,
            "requires_caller_owned_transaction": (
                self.requires_caller_owned_transaction
            ),
            "allows_core_database_driver": self.allows_core_database_driver,
            "allows_core_schema_ddl": self.allows_core_schema_ddl,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "PersistenceHandoffContract":
        if set(value) != {
            "kind",
            "requires_unit_of_work",
            "requires_caller_owned_transaction",
            "allows_core_database_driver",
            "allows_core_schema_ddl",
            "enforcement_owner",
        }:
            raise InvalidPersistenceBoundaryContract(
                "handoff descriptor has unexpected keys"
            )
        try:
            return cls(
                kind=PersistenceHandoffKind(_text(value["kind"], "kind")),
                requires_unit_of_work=_bool(
                    value["requires_unit_of_work"],
                    "requires_unit_of_work",
                ),
                requires_caller_owned_transaction=_bool(
                    value["requires_caller_owned_transaction"],
                    "requires_caller_owned_transaction",
                ),
                allows_core_database_driver=_bool(
                    value["allows_core_database_driver"],
                    "allows_core_database_driver",
                ),
                allows_core_schema_ddl=_bool(
                    value["allows_core_schema_ddl"],
                    "allows_core_schema_ddl",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidPersistenceBoundaryContract(str(error)) from error


@dataclass(frozen=True)
class PersistenceBoundaryContractSet:
    """Closed persistence and mutation handoff vocabulary."""

    stores: tuple[DurableStoreContract, ...]
    mutations: tuple[MutationHolderContract, ...]
    handoffs: tuple[PersistenceHandoffContract, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stores, tuple) or not all(
            isinstance(store, DurableStoreContract) for store in self.stores
        ):
            raise InvalidPersistenceBoundaryContract(
                "stores must be DurableStoreContract values"
            )
        if not isinstance(self.mutations, tuple) or not all(
            isinstance(mutation, MutationHolderContract)
            for mutation in self.mutations
        ):
            raise InvalidPersistenceBoundaryContract(
                "mutations must be MutationHolderContract values"
            )
        if not isinstance(self.handoffs, tuple) or not all(
            isinstance(handoff, PersistenceHandoffContract)
            for handoff in self.handoffs
        ):
            raise InvalidPersistenceBoundaryContract(
                "handoffs must be PersistenceHandoffContract values"
            )
        if {store.store for store in self.stores} != set(DurableStoreKind):
            raise InvalidPersistenceBoundaryContract(
                "store set must cover every durable store kind"
            )
        if {handoff.kind for handoff in self.handoffs} != set(PersistenceHandoffKind):
            raise InvalidPersistenceBoundaryContract(
                "handoff set must cover every handoff kind"
            )
        if {
            (mutation.subject, mutation.phase)
            for mutation in self.mutations
        } != {
            (subject, phase)
            for subject in MutationSubjectKind
            for phase in MutationPhaseKind
        }:
            raise InvalidPersistenceBoundaryContract(
                "mutation set must cover every subject and phase"
            )
        object.__setattr__(
            self,
            "stores",
            tuple(sorted(self.stores, key=lambda store: store.store.value)),
        )
        object.__setattr__(
            self,
            "mutations",
            tuple(
                sorted(
                    self.mutations,
                    key=lambda mutation: (
                        mutation.subject.value,
                        mutation.phase.value,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "handoffs",
            tuple(sorted(self.handoffs, key=lambda handoff: handoff.kind.value)),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "persistence-boundary-contract-set",
            "stores": [store.descriptor() for store in self.stores],
            "mutations": [mutation.descriptor() for mutation in self.mutations],
            "handoffs": [handoff.descriptor() for handoff in self.handoffs],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "PersistenceBoundaryContractSet":
        if set(value) != {"kind", "stores", "mutations", "handoffs"}:
            raise InvalidPersistenceBoundaryContract(
                "persistence boundary descriptor has unexpected keys"
            )
        if value["kind"] != "persistence-boundary-contract-set":
            raise InvalidPersistenceBoundaryContract(
                "persistence boundary descriptor has wrong kind"
            )
        stores = value["stores"]
        mutations = value["mutations"]
        handoffs = value["handoffs"]
        if not isinstance(stores, list):
            raise InvalidPersistenceBoundaryContract("stores must be a list")
        if not isinstance(mutations, list):
            raise InvalidPersistenceBoundaryContract("mutations must be a list")
        if not isinstance(handoffs, list):
            raise InvalidPersistenceBoundaryContract("handoffs must be a list")
        return cls(
            stores=tuple(
                DurableStoreContract.from_descriptor(_mapping(store, "store"))
                for store in stores
            ),
            mutations=tuple(
                MutationHolderContract.from_descriptor(
                    _mapping(mutation, "mutation")
                )
                for mutation in mutations
            ),
            handoffs=tuple(
                PersistenceHandoffContract.from_descriptor(
                    _mapping(handoff, "handoff")
                )
                for handoff in handoffs
            ),
        )


def canonical_persistence_boundary_contract_set() -> PersistenceBoundaryContractSet:
    """Return the pure persistence and mutation handoff contract."""

    return PersistenceBoundaryContractSet(
        stores=tuple(
            DurableStoreContract(
                store=store,
                service_role=_STORE_SERVICE_ROLES[store],
                participation=StoreParticipation.READ_WRITE,
                ordering_policy=_STORE_ORDERING[store],
                accepts_secret_values=False,
                stores_never_commit=True,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            )
            for store in DurableStoreKind
        ),
        mutations=tuple(
            MutationHolderContract(
                subject=subject,
                phase=phase,
                requires_candidate=phase
                in {
                    MutationPhaseKind.PUBLISH,
                    MutationPhaseKind.REPLAY_IDENTITY,
                    MutationPhaseKind.CLEANUP_SUPERSEDED,
                },
                publishes_values=False,
                failure_visibility=_failure_visibility_for_phase(phase),
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            )
            for subject in MutationSubjectKind
            for phase in MutationPhaseKind
        ),
        handoffs=tuple(
            PersistenceHandoffContract(
                kind=kind,
                requires_unit_of_work=True,
                requires_caller_owned_transaction=True,
                allows_core_database_driver=False,
                allows_core_schema_ddl=False,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            )
            for kind in PersistenceHandoffKind
        ),
    )


def _failure_visibility_for_phase(
    phase: MutationPhaseKind,
) -> FailureVisibilityPolicy:
    if phase in {
        MutationPhaseKind.PREPARE_CANDIDATE,
        MutationPhaseKind.VALIDATE_ASSIGNMENTS,
    }:
        return FailureVisibilityPolicy.PRESERVE_PRIOR_PROJECTION
    if phase in {
        MutationPhaseKind.CLEANUP_SUPERSEDED,
        MutationPhaseKind.PRESERVE_RETAINED,
    }:
        return FailureVisibilityPolicy.OPERATOR_VISIBLE_UNCERTAINTY
    if phase is MutationPhaseKind.PUBLISH:
        return FailureVisibilityPolicy.ROLLBACK_ALL_PARTICIPANTS
    return FailureVisibilityPolicy.BOUNDED_EVIDENCE_NO_VALUES


def _validate_bool(value: bool, field: str) -> None:
    if type(value) is not bool:
        raise InvalidPersistenceBoundaryContract(f"{field} must be bool")


def _validate_owner(owner: ContractEnforcementOwner, context: str) -> None:
    if owner is not ContractEnforcementOwner.OPERATIONS:
        raise InvalidPersistenceBoundaryContract(f"{context} belongs to operations")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidPersistenceBoundaryContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidPersistenceBoundaryContract(f"{field} must be bool")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidPersistenceBoundaryContract(f"{field} must be a descriptor")
    return value
