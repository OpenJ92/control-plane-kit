"""Operations-owned interpreter for one bounded gateway rotation phase."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Protocol

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import ExecutionCoordinator
from control_plane_kit_operations.gateway_key_rotation_activation import (
    GatewayKeyRotationActivationProgram,
    ProgressGatewayKeyRotationActivation,
)
from control_plane_kit_operations.gateway_key_rotation_application import (
    GatewayKeyRotationApplicationConflict,
    GatewayKeyRotationProgramView,
    GatewayKeyRotationPublicView,
)
from control_plane_kit_operations.gateway_key_rotation_completion_program import (
    CompleteGatewayKeyRotation,
    GatewayKeyRotationCompletionProgram,
    GatewayKeyRotationRevocationAdapter,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_execution import (
    GatewayKeyRotationOverlapExecutionProgram,
    ProgressGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_program import (
    GatewayKeyGenerationResult,
    GatewayKeyRotationGenerationProgram,
    PrepareGatewayKeyRotationGeneration,
    SubmitGatewayKeyRotationGeneration,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_execution import (
    GatewayKeyRotationRetirementExecutionProgram,
    ProgressGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationProgram,
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    gateway_key_rotation_read_model,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority


class GatewayKeyGenerationAdapter(Protocol):
    """Provider effect returning closed certainty and secret-free evidence."""

    def generate(self, grant: object) -> GatewayKeyGenerationResult: ...


@dataclass(frozen=True)
class _SettledLineage:
    authored_graph_id: str
    realized_projection_id: str
    desired_revision: int


class GatewayKeyRotationProgramExecutor:
    """Advance the phase represented by one durable expected version."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        generation_adapter: GatewayKeyGenerationAdapter,
        revocation_adapter: GatewayKeyRotationRevocationAdapter,
        coordinator: ExecutionCoordinator,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        lease_expiry_clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._generation_adapter = generation_adapter
        self._clock = clock
        self._lease_expiry_clock = lease_expiry_clock
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )
        self._generation = GatewayKeyRotationGenerationProgram(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )
        self._overlap_preparation = GatewayKeyRotationOverlapPreparationProgram(
            unit_of_work_factory,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            id_factory=id_factory,
        )
        self._overlap_execution = GatewayKeyRotationOverlapExecutionProgram(
            unit_of_work_factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            id_factory=id_factory,
        )
        self._activation = GatewayKeyRotationActivationProgram(
            unit_of_work_factory,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
        )
        self._retirement_preparation = GatewayKeyRotationRetirementPreparationProgram(
            unit_of_work_factory,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            id_factory=id_factory,
        )
        self._retirement_execution = GatewayKeyRotationRetirementExecutionProgram(
            unit_of_work_factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
            id_factory=id_factory,
        )
        self._completion = GatewayKeyRotationCompletionProgram(
            unit_of_work_factory,
            revocation_adapter=revocation_adapter,
            clock=clock,
            trusted_epoch_clock=trusted_epoch_clock,
        )

    def advance(
        self,
        rotation: GatewayKeyRotation,
        *,
        expected_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> GatewayKeyRotationProgramView:
        prepared = self._prepare_command(
            rotation.rotation_id,
            expected_version,
            actor_id,
            idempotency_key,
        )
        if isinstance(prepared, GatewayKeyRotationProgramView):
            return prepared
        phase, phase_version, fingerprint = prepared
        current = self._rotations.get(rotation.rotation_id)
        worker = ExecutionWorkerAuthority(
            f"gkrot-worker-{current.rotation_id[-24:]}",
            (PolicyScope.EXECUTION_OPERATE,),
        )
        try:
            if phase == "generation":
                action = self._generation.prepare(
                    PrepareGatewayKeyRotationGeneration(
                        rotation_id=current.rotation_id,
                        expected_version=phase_version,
                        actor_subject=actor_id,
                        prepared_by=actor_id,
                        prepared_at=self._clock(),
                        actor_scopes=(
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.DELEGATION_KEY_GENERATE,
                        ),
                    )
                )
                effect = self._generation_adapter.generate(action.grant)
                result = self._generation.submit(
                    SubmitGatewayKeyRotationGeneration(
                        action=action,
                        result=effect,
                        submitted_by=actor_id,
                        submitted_at=self._clock(),
                        actor_scopes=(
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.DELEGATION_KEY_REGISTER,
                        ),
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(
                        result.rotation,
                        "generation",
                        result.outcome.value,
                        result.replayed,
                    ),
                )
            if phase == "overlap-preparation":
                lineage = self._settled_lineage(current.workspace_id)
                result = self._overlap_preparation.prepare(
                    PrepareGatewayKeyRotationOverlap(
                        rotation_id=current.rotation_id,
                        expected_rotation_version=phase_version,
                        expected_authored_graph_id=lineage.authored_graph_id,
                        expected_current_realized_projection_id=(
                            lineage.realized_projection_id
                        ),
                        expected_desired_realized_projection_id=(
                            lineage.realized_projection_id
                        ),
                        expected_desired_graph_revision=lineage.desired_revision,
                        actor_id=actor_id,
                        actor_scopes=(
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.PLAN_EXECUTE,
                        ),
                        worker_authority=worker,
                        lease_expires_at=self._lease_expiry_clock(),
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(result.rotation, phase, result.outcome.value),
                )
            if phase == "overlap-execution":
                result = self._overlap_execution.progress(
                    ProgressGatewayKeyRotationOverlap(
                        current.rotation_id,
                        phase_version,
                        actor_id,
                        (PolicyScope.DELEGATION_KEY_ROTATE,),
                        worker,
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(result.rotation, phase, result.outcome.value),
                )
            if phase == "activation":
                result = self._activation.progress(
                    ProgressGatewayKeyRotationActivation(
                        current.rotation_id,
                        phase_version,
                        actor_id,
                        (
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.DELEGATION_KEY_ACTIVATE,
                        ),
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(result.rotation, phase, result.outcome.value),
                )
            if phase == "drain-or-retirement":
                activation = self._activation.progress(
                    ProgressGatewayKeyRotationActivation(
                        current.rotation_id,
                        self._activation_base_version(current),
                        actor_id,
                        (
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.DELEGATION_KEY_ACTIVATE,
                        ),
                    )
                )
                if activation.outcome.value == "waiting":
                    return self._complete_command(
                        current.rotation_id,
                        idempotency_key,
                        fingerprint,
                        self._result(
                            activation.rotation,
                            "grant-drain",
                            activation.outcome.value,
                        ),
                    )
                lineage = self._settled_lineage(current.workspace_id)
                result = self._retirement_preparation.prepare(
                    PrepareGatewayKeyRotationRetirement(
                        rotation_id=current.rotation_id,
                        expected_rotation_version=phase_version,
                        expected_authored_graph_id=lineage.authored_graph_id,
                        expected_current_realized_projection_id=(
                            lineage.realized_projection_id
                        ),
                        expected_desired_realized_projection_id=(
                            lineage.realized_projection_id
                        ),
                        expected_desired_graph_revision=lineage.desired_revision,
                        actor_id=actor_id,
                        actor_scopes=(
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.PLAN_EXECUTE,
                        ),
                        worker_authority=worker,
                        lease_expires_at=self._lease_expiry_clock(),
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(
                        result.rotation,
                        "retirement-preparation",
                        result.outcome.value,
                    ),
                )
            if phase == "retirement-execution":
                result = self._retirement_execution.progress(
                    ProgressGatewayKeyRotationRetirement(
                        current.rotation_id,
                        phase_version,
                        actor_id,
                        (PolicyScope.DELEGATION_KEY_ROTATE,),
                        worker,
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(result.rotation, phase, result.outcome.value),
                )
            if phase == "completion":
                result = self._completion.progress(
                    CompleteGatewayKeyRotation(
                        current.rotation_id,
                        phase_version,
                        actor_id,
                        (
                            PolicyScope.DELEGATION_KEY_ROTATE,
                            PolicyScope.DELEGATION_KEY_RETIRE,
                            PolicyScope.DELEGATION_KEY_REVOKE,
                            PolicyScope.SECRET_PROVIDER_REVOKE,
                        ),
                    )
                )
                return self._complete_command(
                    current.rotation_id,
                    idempotency_key,
                    fingerprint,
                    self._result(result.rotation, phase, result.outcome.value),
                )
        except GatewayKeyRotationApplicationConflict:
            raise
        except ValueError as error:
            raise GatewayKeyRotationApplicationConflict(str(error)) from error
        raise GatewayKeyRotationApplicationConflict(
            f"rotation phase {phase} is not publicly advanceable"
        )

    def _prepare_command(
        self,
        rotation_id: str,
        expected_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> tuple[str, int, str] | GatewayKeyRotationProgramView:
        fingerprint = sha256(
            json.dumps(
                {
                    "rotation_id": rotation_id,
                    "expected_version": expected_version,
                    "actor_id": actor_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._unit_of_work_factory() as unit_of_work:
            store = unit_of_work.stores.gateway_key_rotations
            store.lock_program_command(rotation_id, idempotency_key)
            existing = store.program_command(rotation_id, idempotency_key)
            if existing is not None:
                if existing["intent_fingerprint"] != fingerprint:
                    raise GatewayKeyRotationApplicationConflict(
                        "rotation advance idempotency key was reused with different intent"
                    )
                if existing["state"] == "completed":
                    result = GatewayKeyRotationProgramView.from_descriptor(
                        existing["result_descriptor"],
                        replayed=True,
                    )
                    unit_of_work.commit()
                    return result
                unit_of_work.commit()
                return (
                    str(existing["phase"]),
                    int(existing["expected_version"]),
                    fingerprint,
                )
            current = store.get_for_update(rotation_id)
            if current.version != expected_version:
                raise GatewayKeyRotationApplicationConflict(
                    "rotation expected version is stale"
                )
            pending = store.pending_program_command(rotation_id)
            if pending is not None:
                raise GatewayKeyRotationApplicationConflict(
                    "rotation has an unfinished advance command"
                )
            phase = self._phase_for_status(current.status)
            requested_at = self._clock()
            store.add_program_command(
                rotation_id=rotation_id,
                idempotency_key=idempotency_key,
                intent_fingerprint=fingerprint,
                expected_version=expected_version,
                phase=phase,
                requested_by=actor_id,
                requested_at=requested_at,
            )
            unit_of_work.commit()
        return phase, expected_version, fingerprint

    def _complete_command(
        self,
        rotation_id: str,
        idempotency_key: str,
        fingerprint: str,
        result: GatewayKeyRotationProgramView,
    ) -> GatewayKeyRotationProgramView:
        with self._unit_of_work_factory() as unit_of_work:
            store = unit_of_work.stores.gateway_key_rotations
            store.lock_program_command(rotation_id, idempotency_key)
            existing = store.program_command(rotation_id, idempotency_key)
            if existing is None or existing["intent_fingerprint"] != fingerprint:
                raise GatewayKeyRotationApplicationConflict(
                    "rotation advance command receipt is missing"
                )
            if existing["state"] == "completed":
                replay = GatewayKeyRotationProgramView.from_descriptor(
                    existing["result_descriptor"],
                    replayed=True,
                )
                unit_of_work.commit()
                return replay
            store.complete_program_command(
                rotation_id=rotation_id,
                idempotency_key=idempotency_key,
                intent_fingerprint=fingerprint,
                result_descriptor=result.descriptor(),
                completed_at=self._clock(),
            )
            unit_of_work.commit()
        return result

    @staticmethod
    def _phase_for_status(status: GatewayKeyRotationStatus) -> str:
        phases = {
            GatewayKeyRotationStatus.APPROVED: "generation",
            GatewayKeyRotationStatus.KEY_GENERATED: "overlap-preparation",
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING: "overlap-execution",
            GatewayKeyRotationStatus.OVERLAP_READY: "activation",
            GatewayKeyRotationStatus.DRAINING_OLD_GRANTS: "drain-or-retirement",
            GatewayKeyRotationStatus.RETIREMENT_DEPLOYING: "retirement-execution",
            GatewayKeyRotationStatus.RETIREMENT_READY: "completion",
        }
        try:
            return phases[status]
        except KeyError as error:
            raise GatewayKeyRotationApplicationConflict(
                f"rotation status {status.value} is not publicly advanceable"
            ) from error

    def _activation_base_version(self, current: GatewayKeyRotation) -> int:
        for transition in self._rotations.transitions(current.rotation_id):
            if transition.to_status is GatewayKeyRotationStatus.NEW_KEY_ACTIVE:
                return transition.from_version
        raise GatewayKeyRotationApplicationConflict(
            "rotation drain lacks activation lineage"
        )

    def _settled_lineage(self, workspace_id: str) -> _SettledLineage:
        with self._unit_of_work_factory() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get(workspace_id)
            unit_of_work.commit()
        if (
            workspace.current_graph_id is None
            or workspace.current_graph_id != workspace.desired_graph_id
            or workspace.current_realized_projection_id is None
            or workspace.current_realized_projection_id
            != workspace.desired_realized_projection_id
        ):
            raise GatewayKeyRotationApplicationConflict(
                "gateway rotation requires settled current graph lineage"
            )
        return _SettledLineage(
            workspace.current_graph_id,
            workspace.current_realized_projection_id,
            workspace.desired_graph_revision,
        )

    @staticmethod
    def _result(
        current: GatewayKeyRotation,
        phase: str,
        outcome: str,
        replayed: bool = False,
    ) -> GatewayKeyRotationProgramView:
        return GatewayKeyRotationProgramView(
            GatewayKeyRotationPublicView.from_read_model(
                gateway_key_rotation_read_model(current)
            ),
            phase,
            outcome,
            replayed,
        )


__all__ = [
    "GatewayKeyGenerationAdapter",
    "GatewayKeyRotationProgramExecutor",
]
