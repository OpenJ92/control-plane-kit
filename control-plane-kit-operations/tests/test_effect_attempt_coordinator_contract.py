from __future__ import annotations

from dataclasses import fields, replace
import inspect
import json
import os
from pathlib import Path
from typing import get_type_hints, Protocol
import unittest

import control_plane_kit_architecture_testing as architecture_testing
import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import EffectAttemptFence
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
)
from control_plane_kit_core.planning import (
    AllocatePublicIngress,
    ActivityId,
    PublicIngressActivityTarget,
    SocketConnectionTarget,
    SwitchSocketConnection,
)
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
    runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.coordinator import (
    ActivityExecutionAdapter,
    ActivityExecutionDispatcher,
    ActivityExecutionOutcome,
    ActivityRealizationContext,
    CoordinatorStatus,
    ExecutionCoordinator,
    ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied,
    ExecutionCoordinatorNotFound,
    RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    ExistingFold,
    FoldEffectAttempt,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
    ReconcileEffectAttempt,
)
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict,
    EffectAttemptStartDenied,
    EffectAttemptStartNotFound,
    ExistingAttempt,
    NewlyStarted,
    StartEffectAttempt,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.records import ActivityEventRecord
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_coordinator_fixture import (
    EffectAttemptCoordinatorFixture,
    RecordingCoordinatorAdapter,
    RecordingFoldService,
    RecordingReconciliationService,
    RecordingStartService,
)
from tests.test_runtime_interpreter_dispatcher import context_for


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_SOURCE = (
    PACKAGE_ROOT / "src" / "control_plane_kit_operations" / "coordinator.py"
)
RUNTIME_EFFECTS_SOURCE = (
    PACKAGE_ROOT / "src" / "control_plane_kit_operations" / "runtime_effects.py"
)
COORDINATOR_SOURCE_PATH = "control_plane_kit_operations/coordinator.py"
RUNTIME_EFFECTS_SOURCE_PATH = "control_plane_kit_operations/runtime_effects.py"
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT / "docs" / "architecture" / "package-module-inventory.json",
    )
)
COORDINATOR_MODULE = "control_plane_kit_operations.coordinator"
RUNTIME_EFFECTS_MODULE = "control_plane_kit_operations.runtime_effects"
COORDINATOR_EXPORTS = {
    "ActivityExecutionAdapter",
    "ActivityExecutionDispatcher",
    "ActivityExecutionOutcome",
    "ActivityRealizationContext",
    "CoordinatorStatus",
    "ExecuteActivityRun",
    "ExecutionCoordinator",
    "ExecutionCoordinatorConflict",
    "ExecutionCoordinatorDenied",
    "ExecutionCoordinatorError",
    "ExecutionCoordinatorNotFound",
    "ExecutionCoordinatorResult",
    "RuntimeEffectInterpreter",
    "RuntimeInterpreterDispatcher",
}
COORDINATOR_DEPENDENCIES = {
    "control_plane_kit_core.operations",
    "control_plane_kit_core.operations.execution",
    "control_plane_kit_core.operations.lifecycle",
    "control_plane_kit_core.planning",
    "control_plane_kit_core.planning.saga",
    "control_plane_kit_core.policies",
    "control_plane_kit_core.probe_intents",
    "control_plane_kit_core.runtime_effect_observation",
    "control_plane_kit_core.runtime_effects",
    "control_plane_kit_core.secrets",
    "control_plane_kit_core.topology",
    "control_plane_kit_core.types",
    "control_plane_kit_operations.activity_journal",
    "control_plane_kit_operations.effect_attempt_fold",
    "control_plane_kit_operations.effect_attempt_fold_interpreter",
    "control_plane_kit_operations.effect_attempt_reconciliation",
    "control_plane_kit_operations.effect_attempt_reconciliation_interpreter",
    "control_plane_kit_operations.effect_attempt_start",
    "control_plane_kit_operations.effect_attempt_start_interpreter",
    "control_plane_kit_operations.effect_attempts",
    "control_plane_kit_operations.effect_outcome_evidence",
    "control_plane_kit_operations.execution_leases",
    "control_plane_kit_operations.ingress_authorities",
    "control_plane_kit_operations.lifecycle",
    "control_plane_kit_operations.products",
    "control_plane_kit_operations.records",
    "control_plane_kit_operations.runtime_authorities",
    "control_plane_kit_operations.runtime_effects",
    "control_plane_kit_operations.secret_providers",
    "control_plane_kit_operations.workflows",
}
RUNTIME_EFFECTS_DEPENDENCIES = {
    "control_plane_kit_core.environment",
    "control_plane_kit_core.operations",
    "control_plane_kit_core.planning.activity_plan",
    "control_plane_kit_core.products",
    "control_plane_kit_core.probe_intents",
    "control_plane_kit_core.public_ingress",
    "control_plane_kit_core.runtime_authority",
    "control_plane_kit_core.runtime_effect_observation",
    "control_plane_kit_core.runtime_effects",
    "control_plane_kit_core.secrets",
    "control_plane_kit_core.topology",
    "control_plane_kit_core.types",
    "control_plane_kit_core.verification",
    "control_plane_kit_operations.coordinator",
    "control_plane_kit_operations.ingress_authorities",
    "control_plane_kit_operations.products",
    "control_plane_kit_operations.runtime_authorities",
    "control_plane_kit_operations.workflows",
}


def _exact_imports(*rows: tuple[str, str | None, str | None]):
    return tuple(
        architecture_testing.ImportSurfaceEntry(*row)
        for row in rows
    )


EXACT_COORDINATOR_IMPORTS = _exact_imports(
    ("__future__", "annotations", None),
    ("control_plane_kit_core.operations", "EffectAttemptIdentity", None),
    ("control_plane_kit_core.operations", "EffectAttemptTransition", None),
    ("control_plane_kit_core.operations", "EffectAttemptTransitionKind", None),
    ("control_plane_kit_core.operations", "RunId", None),
    ("control_plane_kit_core.operations.execution", "EffectResultKind", None),
    ("control_plane_kit_core.operations.lifecycle", "ActivityEventKind", None),
    ("control_plane_kit_core.operations.lifecycle", "ActivityRunStatus", None),
    ("control_plane_kit_core.operations.lifecycle", "ExecutionRequestStatus", None),
    ("control_plane_kit_core.operations.lifecycle", "FailureCategory", None),
    ("control_plane_kit_core.planning", "ActivityId", None),
    ("control_plane_kit_core.planning", "ActivityPlan", None),
    ("control_plane_kit_core.planning", "AddSocketConnection", None),
    ("control_plane_kit_core.planning", "AllocatePublicIngress", None),
    ("control_plane_kit_core.planning", "PlannedActivity", None),
    ("control_plane_kit_core.planning", "RemovePublicIngress", None),
    ("control_plane_kit_core.planning", "RemoveSocketConnection", None),
    ("control_plane_kit_core.planning", "SwitchSocketConnection", None),
    ("control_plane_kit_core.planning.saga", "ExecutionSchedule", None),
    ("control_plane_kit_core.planning.saga", "SagaJournalProjection", None),
    ("control_plane_kit_core.planning.saga", "derive_schedule", None),
    ("control_plane_kit_core.planning.saga", "project_activity_journal", None),
    ("control_plane_kit_core.policies", "PolicyScope", None),
    (
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_intent_fingerprint",
        None,
    ),
    (
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_request_for_intent",
        None,
    ),
    ("control_plane_kit_core.runtime_effects", "RuntimeEffectFailure", None),
    ("control_plane_kit_core.runtime_effects", "RuntimeEffectRequest", None),
    ("control_plane_kit_core.runtime_effects", "RuntimeEffectResult", None),
    ("control_plane_kit_core.secrets", "SecretResolutionGrant", None),
    ("control_plane_kit_core.types", "RuntimeKind", None),
    ("control_plane_kit_operations.activity_journal", "activity_journal_events", None),
    ("control_plane_kit_operations.effect_attempt_fold", "EffectAttemptFoldConflict", None),
    ("control_plane_kit_operations.effect_attempt_fold", "EffectAttemptFoldDenied", None),
    ("control_plane_kit_operations.effect_attempt_fold", "EffectAttemptFoldNotFound", None),
    ("control_plane_kit_operations.effect_attempt_fold", "EffectAttemptFoldResult", None),
    ("control_plane_kit_operations.effect_attempt_fold", "ExistingFold", None),
    ("control_plane_kit_operations.effect_attempt_fold", "FoldEffectAttempt", None),
    ("control_plane_kit_operations.effect_attempt_fold", "NewlyFolded", None),
    ("control_plane_kit_operations.effect_attempt_fold_interpreter", "EffectAttemptFoldService", None),
    (
        "control_plane_kit_operations.effect_attempt_reconciliation",
        "EffectAttemptReconciliationConflict",
        None,
    ),
    (
        "control_plane_kit_operations.effect_attempt_reconciliation",
        "EffectAttemptReconciliationDenied",
        None,
    ),
    (
        "control_plane_kit_operations.effect_attempt_reconciliation",
        "EffectAttemptReconciliationNotFound",
        None,
    ),
    (
        "control_plane_kit_operations.effect_attempt_reconciliation",
        "ReconcileEffectAttempt",
        None,
    ),
    (
        "control_plane_kit_operations.effect_attempt_reconciliation_interpreter",
        "EffectAttemptReconciliationService",
        None,
    ),
    ("control_plane_kit_operations.effect_attempt_start", "EffectAttemptStartConflict", None),
    ("control_plane_kit_operations.effect_attempt_start", "EffectAttemptStartDenied", None),
    ("control_plane_kit_operations.effect_attempt_start", "EffectAttemptStartNotFound", None),
    ("control_plane_kit_operations.effect_attempt_start", "EffectAttemptStartResult", None),
    ("control_plane_kit_operations.effect_attempt_start", "ExistingAttempt", None),
    ("control_plane_kit_operations.effect_attempt_start", "NewlyStarted", None),
    ("control_plane_kit_operations.effect_attempt_start", "StartEffectAttempt", None),
    ("control_plane_kit_operations.effect_attempt_start_interpreter", "EffectAttemptStartService", None),
    ("control_plane_kit_operations.effect_attempts", "EffectAttemptRecord", None),
    ("control_plane_kit_operations.effect_outcome_evidence", "ExecutionEffectOutcome", None),
    ("control_plane_kit_operations.effect_outcome_evidence", "effect_outcome_failure", None),
    ("control_plane_kit_operations.effect_outcome_evidence", "effect_outcome_transition", None),
    ("control_plane_kit_operations.execution_leases", "ExecutionLeaseFence", None),
    ("control_plane_kit_operations.ingress_authorities", "CloudflareOwnedIngressResource", None),
    ("control_plane_kit_operations.ingress_authorities", "GeneratedIngressSecretReference", None),
    ("control_plane_kit_operations.ingress_authorities", "RegisteredIngressAuthority", None),
    ("control_plane_kit_operations.lifecycle", "CompleteActivityRun", None),
    ("control_plane_kit_operations.lifecycle", "ExecutionWorkerAuthority", None),
    ("control_plane_kit_operations.lifecycle", "FailActivityRun", None),
    ("control_plane_kit_operations.lifecycle", "RunLifecycleCommandService", None),
    ("control_plane_kit_operations.lifecycle", "RunLifecycleConflict", None),
    ("control_plane_kit_operations.products", "RegisteredImagePullAuthority", None),
    ("control_plane_kit_operations.products", "RegisteredProduct", None),
    ("control_plane_kit_operations.records", "ActivityEventRecord", None),
    ("control_plane_kit_operations.records", "ActivityPlanRecord", None),
    ("control_plane_kit_operations.records", "ActivityRunRecord", None),
    ("control_plane_kit_operations.records", "BoundedEvidence", None),
    ("control_plane_kit_operations.records", "CoordinatorStatus", None),
    ("control_plane_kit_operations.records", "ExecutionCommandReceiptRecord", None),
    ("control_plane_kit_operations.records", "ExecutionCommandReceiptStatus", None),
    ("control_plane_kit_operations.records", "ExecutionCommandResultRecord", None),
    ("control_plane_kit_operations.records", "ExecutionRequestRecord", None),
    ("control_plane_kit_operations.records", "FailureEvidence", None),
    ("control_plane_kit_operations.records", "ObservationRecord", None),
    ("control_plane_kit_operations.records", "OperationsRecordError", None),
    ("control_plane_kit_operations.records", "RealizedGraphProjectionRecord", None),
    ("control_plane_kit_operations.records", "execution_command_intent_fingerprint", None),
    ("control_plane_kit_operations.runtime_authorities", "RegisteredRuntimeAuthority", None),
    (
        "control_plane_kit_operations.runtime_authorities",
        "RegisteredRuntimeAuthorityDelivery",
        None,
    ),
    ("control_plane_kit_operations.runtime_effects", "_runtime_effect_intent_for_context", None),
    ("control_plane_kit_operations.runtime_effects", "required_secret_uses_for_runtime_effect", None),
    ("control_plane_kit_operations.secret_providers", "AuthorizeSecretUse", None),
    ("control_plane_kit_operations.secret_providers", "SecretProviderRegistrationError", None),
    ("control_plane_kit_operations.secret_providers", "SecretUseResolutionAuthorizer", None),
    ("control_plane_kit_operations.secret_providers", "secret_use_correlation_for", None),
    ("control_plane_kit_operations.workflows", "IdempotencyKey", None),
    ("control_plane_kit_operations.workflows", "InvalidOperationCommand", None),
    ("dataclasses", "dataclass", None),
    ("dataclasses", "field", None),
    ("dataclasses", "replace", None),
    ("enum", "StrEnum", None),
    ("typing", "Any", None),
    ("typing", "Callable", None),
    ("typing", "Mapping", None),
    ("typing", "Protocol", None),
)

EXACT_RUNTIME_EFFECTS_IMPORTS = _exact_imports(
    ("__future__", "annotations", None),
    ("control_plane_kit_core.environment", "PublicStaticEnvironmentBinding", None),
    ("control_plane_kit_core.operations", "RunId", None),
    ("control_plane_kit_core.planning.activity_plan", "AddSocketConnection", None),
    ("control_plane_kit_core.planning.activity_plan", "NodeTarget", None),
    ("control_plane_kit_core.planning.activity_plan", "ReconcileNode", None),
    ("control_plane_kit_core.planning.activity_plan", "RemoveNodeResource", None),
    ("control_plane_kit_core.planning.activity_plan", "RemoveRuntimeResource", None),
    ("control_plane_kit_core.planning.activity_plan", "RemoveSocketConnection", None),
    ("control_plane_kit_core.planning.activity_plan", "StartNode", None),
    ("control_plane_kit_core.planning.activity_plan", "StopNode", None),
    ("control_plane_kit_core.planning.activity_plan", "StopRuntime", None),
    ("control_plane_kit_core.planning.activity_plan", "SwitchSocketConnection", None),
    ("control_plane_kit_core.planning.activity_plan", "WaitForHealthy", None),
    ("control_plane_kit_core.probe_intents", "EndpointContext", None),
    ("control_plane_kit_core.probe_intents", "LiteralEndpointMaterial", None),
    ("control_plane_kit_core.probe_intents", "RuntimeEndpointObservation", None),
    ("control_plane_kit_core.products", "ProductDescriptorDigest", None),
    ("control_plane_kit_core.products", "ProductIdentity", None),
    ("control_plane_kit_core.products", "ProductReference", None),
    ("control_plane_kit_core.public_ingress", "PublicIngressExposure", None),
    ("control_plane_kit_core.runtime_authority", "RuntimeAuthorityAccessDelivery", None),
    ("control_plane_kit_core.runtime_authority", "RuntimeAuthorityReference", None),
    ("control_plane_kit_core.runtime_effect_observation", "RuntimeEffectIntent", None),
    ("control_plane_kit_core.runtime_effect_observation", "RuntimeEffectIntentSource", None),
    (
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_request_for_intent",
        None,
    ),
    ("control_plane_kit_core.runtime_effects", "GatewayHttpTarget", None),
    ("control_plane_kit_core.runtime_effects", "GatewayPostgresTarget", None),
    ("control_plane_kit_core.runtime_effects", "GatewayTarget", None),
    ("control_plane_kit_core.runtime_effects", "GatewayTargetId", None),
    ("control_plane_kit_core.runtime_effects", "GatewayTargetMap", None),
    ("control_plane_kit_core.runtime_effects", "ImagePullAuthority", None),
    ("control_plane_kit_core.runtime_effects", "RuntimeEffectKind", None),
    ("control_plane_kit_core.runtime_effects", "RuntimeEffectRequest", None),
    ("control_plane_kit_core.runtime_effects", "RuntimeProductMaterial", None),
    ("control_plane_kit_core.secrets", "SecretDelivery", None),
    ("control_plane_kit_core.secrets", "SecretEnvironmentDelivery", None),
    ("control_plane_kit_core.secrets", "SecretFileDelivery", None),
    ("control_plane_kit_core.secrets", "SecretReference", None),
    ("control_plane_kit_core.secrets", "SecretUseIntent", None),
    ("control_plane_kit_core.secrets", "secret_delivery_sort_key", None),
    ("control_plane_kit_core.topology", "DEFAULT_GRAPH_CODEC", None),
    ("control_plane_kit_core.topology", "DeploymentGraph", None),
    ("control_plane_kit_core.topology", "Node", None),
    ("control_plane_kit_core.types", "Protocol", None),
    ("control_plane_kit_core.types", "RuntimeKind", None),
    ("control_plane_kit_core.verification", "PostgresQueryCheck", None),
    ("control_plane_kit_operations.coordinator", "ActivityRealizationContext", None),
    ("control_plane_kit_operations.coordinator", "_CoordinatorContext", None),
    ("control_plane_kit_operations.ingress_authorities", "CloudflareOwnedIngressResource", None),
    ("control_plane_kit_operations.ingress_authorities", "GeneratedIngressSecretReference", None),
    ("control_plane_kit_operations.ingress_authorities", "GeneratedSecretPurpose", None),
    ("control_plane_kit_operations.ingress_authorities", "OwnedIngressResourceStatus", None),
    ("control_plane_kit_operations.ingress_authorities", "RegisteredIngressAuthority", None),
    (
        "control_plane_kit_operations.ingress_authorities",
        "cloudflare_tunnel_token_delivery_plan",
        None,
    ),
    ("control_plane_kit_operations.products", "RegisteredImagePullAuthority", None),
    ("control_plane_kit_operations.products", "RegisteredProduct", None),
    ("control_plane_kit_operations.runtime_authorities", "RegisteredRuntimeAuthority", None),
    (
        "control_plane_kit_operations.runtime_authorities",
        "RegisteredRuntimeAuthorityDelivery",
        None,
    ),
    ("control_plane_kit_operations.runtime_authorities", "RemoteDockerTlsAuthority", None),
    ("control_plane_kit_operations.workflows", "InvalidOperationCommand", None),
    ("dataclasses", "replace", None),
    ("json", None, None),
    ("typing", "Mapping", None),
)


def _exact_calls(*rows: tuple[str | None, int]):
    targets = []
    for value, count in rows:
        target_type = (
            architecture_testing.UnresolvedCallTarget
            if value is None
            else architecture_testing.ResolvedCallTarget
        )
        for _ in range(count):
            targets.append(target_type() if value is None else target_type(value))
    return tuple(targets)


EXACT_COORDINATOR_CALLS = _exact_calls(
    ("ActivityExecutionOutcome.succeeded", 1),
    ("ActivityExecutionOutcome.uncertain", 3),
    ("ActivityExecutionOutcome.unsupported", 1),
    ("ActivityRealizationContext", 1),
    ("ExecutionCommandReceiptRecord", 1),
    ("ExecutionCommandResultRecord", 1),
    ("ExecutionCoordinatorConflict", 27),
    ("ExecutionCoordinatorDenied", 6),
    ("ExecutionCoordinatorNotFound", 8),
    ("ExecutionCoordinatorResult", 16),
    ("_CoordinatorContext", 1),
    ("_command_result_record", 1),
    ("_coordinator_result", 1),
    ("_execution_command_fingerprint", 2),
    ("_get_request_for_update", 1),
    ("_get_run", 2),
    ("_get_run_for_update", 1),
    ("_is_socket_connection_operation", 1),
    ("_locked_request_and_run", 3),
    ("_outcome_event_kind", 1),
    ("_require_operate_scope", 1),
    ("_require_run_id", 1),
    ("_require_worker_owns", 1),
    ("_runtime_authority_for_request", 1),
    ("_socket_connection_outcome", 1),
    ("_step_evidence", 3),
    ("_uncertain_runtime_result", 2),
    ("_unsupported_runtime_result", 5),
    ("_validate_observations", 1),
    ("all", 8),
    ("any", 1),
    ("cls", 4),
    ("context.plan.activity", 1),
    ("context.realization_context", 1),
    ("control_plane_kit_core.operations.EffectAttemptIdentity", 1),
    ("control_plane_kit_core.operations.EffectAttemptTransition", 1),
    ("control_plane_kit_core.operations.RunId", 1),
    ("control_plane_kit_core.planning.ActivityId", 1),
    ("control_plane_kit_core.planning.saga.derive_schedule", 1),
    ("control_plane_kit_core.planning.saga.project_activity_journal", 1),
    (
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_intent_fingerprint",
        1,
    ),
    (
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_request_for_intent",
        1,
    ),
    ("control_plane_kit_core.runtime_effects.RuntimeEffectFailure", 2),
    ("control_plane_kit_core.runtime_effects.RuntimeEffectResult.uncertain", 1),
    ("control_plane_kit_core.runtime_effects.RuntimeEffectResult.unsupported", 1),
    ("control_plane_kit_operations.activity_journal.activity_journal_events", 1),
    ("control_plane_kit_operations.effect_attempt_fold.ExistingFold", 2),
    ("control_plane_kit_operations.effect_attempt_fold.FoldEffectAttempt", 1),
    ("control_plane_kit_operations.effect_attempt_fold.NewlyFolded", 2),
    ("control_plane_kit_operations.effect_attempt_reconciliation.ReconcileEffectAttempt", 1),
    ("control_plane_kit_operations.effect_attempt_start.StartEffectAttempt", 1),
    ("control_plane_kit_operations.effect_attempts.EffectAttemptRecord", 1),
    (
        "control_plane_kit_operations.effect_outcome_evidence."
        "ExecutionEffectOutcome",
        1,
    ),
    (
        "control_plane_kit_operations.effect_outcome_evidence."
        "effect_outcome_failure",
        1,
    ),
    (
        "control_plane_kit_operations.effect_outcome_evidence."
        "effect_outcome_transition",
        1,
    ),
    ("control_plane_kit_operations.lifecycle.CompleteActivityRun", 1),
    ("control_plane_kit_operations.lifecycle.FailActivityRun", 1),
    ("control_plane_kit_operations.records.ActivityEventRecord", 2),
    ("control_plane_kit_operations.records.BoundedEvidence", 5),
    ("control_plane_kit_operations.records.BoundedEvidence.from_mapping", 7),
    ("control_plane_kit_operations.records.FailureEvidence", 5),
    (
        "control_plane_kit_operations.runtime_effects."
        "_runtime_effect_intent_for_context",
        1,
    ),
    (
        "control_plane_kit_operations.runtime_effects."
        "required_secret_uses_for_runtime_effect",
        1,
    ),
    ("control_plane_kit_operations.secret_providers.AuthorizeSecretUse", 1),
    ("control_plane_kit_operations.secret_providers.secret_use_correlation_for", 1),
    ("control_plane_kit_operations.workflows.IdempotencyKey", 2),
    ("control_plane_kit_operations.workflows.InvalidOperationCommand", 62),
    ("dataclasses.dataclass", 7),
    ("dataclasses.field", 1),
    ("dataclasses.replace", 1),
    ("details.descriptor", 1),
    ("execute_with_authority", 1),
    ("getattr", 3),
    ("grant.permits", 1),
    ("grants.append", 1),
    ("hasattr", 4),
    ("interpreter.execute", 1),
    ("isinstance", 31),
    ("object", 1),
    ("object.__setattr__", 9),
    ("range", 1),
    ("self._admit_command", 1),
    ("self._adapter.execute", 1),
    ("self._adapter.execute_runtime", 1),
    ("self._authorize_secret_resolutions", 1),
    ("self._classify_current", 3),
    ("self._clock", 4),
    ("self._complete_command", 1),
    ("self._execute_admitted", 1),
    ("self._fold_service.execute", 1),
    ("self._fresh_run", 1),
    ("self._id_factory", 2),
    ("self._lifecycle.execute", 2),
    ("self._load_context", 3),
    ("self._reconciliation_service.execute", 1),
    ("self._record_outcome", 1),
    ("self._record_step_event", 1),
    ("self._start_service.execute", 1),
    ("self._unit_of_work_factory", 6),
    ("self.ingress.execute", 1),
    ("self.interpreters.get", 1),
    ("self.interpreters.items", 1),
    ("self.runtime.execute", 1),
    ("self.runtime.execute_runtime", 1),
    ("self.secret_use_authorizer.authorize_resolution", 1),
    ("stores.activity_history.get_plan", 1),
    ("stores.execution.add_command_receipt", 1),
    ("stores.execution.add_event", 2),
    ("stores.execution.command_receipt_for_idempotency", 1),
    ("stores.execution.complete_command_receipt", 1),
    ("stores.execution.events_for_run", 1),
    ("stores.execution.get_request", 1),
    ("stores.execution.get_request_for_update", 1),
    ("stores.execution.get_run", 1),
    ("stores.execution.get_run_for_update", 1),
    ("stores.execution.lock_command_idempotency", 2),
    ("stores.execution.next_event_ordinal", 2),
    ("stores.generated_ingress_secrets.list_for_workspace", 1),
    ("stores.graphs.get", 1),
    ("stores.image_pull_authorities.list_active", 1),
    ("stores.ingress_authorities.list_active", 1),
    ("stores.ingress_resources.list_cloudflare", 1),
    ("stores.observed_state.put", 2),
    ("stores.realized_graphs.get", 2),
    ("stores.realized_graphs.identity_for_authored", 2),
    ("stores.registered_products.list_active", 1),
    ("stores.runtime_authorities.list_active", 1),
    ("stores.runtime_authority_deliveries.list_active", 1),
    ("tuple", 9),
    ("type", 13),
    ("unit_of_work.commit", 4),
    ("value.strip", 1),
)

EXACT_RUNTIME_EFFECTS_CALLS = _exact_calls(
    (None, 1),
    ("_connector_ingress_for_node", 1),
    ("_descriptor_digest", 1),
    ("_gateway_process_target_map_descriptor", 1),
    ("_generated_ingress_secret_for", 1),
    ("_has_tunnel_token_delivery", 1),
    ("_ingress_authority_for", 1),
    ("_ingress_resource_for", 1),
    ("_material_graph", 1),
    ("_node_target", 2),
    ("_postgres_target_details", 1),
    ("_product_identity", 1),
    ("_product_material_for_node", 1),
    ("_products_for_context", 1),
    ("_provider_port_for_socket", 2),
    ("_public_environment_for_node", 1),
    ("_pull_authority_for_product", 1),
    ("_registered_product_for_node", 3),
    ("_runtime_authority_deliveries_for_context", 1),
    ("_runtime_authority_ref_for_context", 1),
    ("_runtime_effect_intent_for_context", 1),
    ("_runtime_id_for_context", 1),
    ("_runtime_kind_for_context", 1),
    ("_secret_deliveries_for_node", 1),
    ("_with_source_edges", 1),
    ("any", 2),
    ("authority.authority.permits", 1),
    ("control_plane_kit_core.environment.PublicStaticEnvironmentBinding", 1),
    ("control_plane_kit_core.operations.RunId", 1),
    ("control_plane_kit_core.probe_intents.LiteralEndpointMaterial", 2),
    ("control_plane_kit_core.probe_intents.RuntimeEndpointObservation", 2),
    ("control_plane_kit_core.products.ProductDescriptorDigest", 1),
    ("control_plane_kit_core.products.ProductIdentity", 1),
    ("control_plane_kit_core.products.ProductReference", 1),
    (
        "control_plane_kit_core.runtime_effect_observation.RuntimeEffectIntent",
        1,
    ),
    (
        "control_plane_kit_core.runtime_effect_observation."
        "RuntimeEffectIntentSource",
        1,
    ),
    (
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_request_for_intent",
        1,
    ),
    ("control_plane_kit_core.runtime_effects.GatewayHttpTarget", 2),
    ("control_plane_kit_core.runtime_effects.GatewayPostgresTarget", 2),
    ("control_plane_kit_core.runtime_effects.GatewayTargetId", 1),
    ("control_plane_kit_core.runtime_effects.GatewayTargetMap", 1),
    ("control_plane_kit_core.runtime_effects.RuntimeProductMaterial", 1),
    ("control_plane_kit_core.topology.DEFAULT_GRAPH_CODEC.decode", 2),
    (
        "control_plane_kit_operations.ingress_authorities."
        "cloudflare_tunnel_token_delivery_plan",
        1,
    ),
    ("control_plane_kit_operations.workflows.InvalidOperationCommand", 30),
    ("dataclasses.replace", 2),
    ("gateway_node.provider_socket", 1),
    ("gateway_target_map_for_node", 1),
    ("getattr", 4),
    ("graph.edges.values", 1),
    ("graph_id.strip", 2),
    ("hasattr", 2),
    ("int", 1),
    ("isinstance", 14),
    ("json.dumps", 1),
    ("len", 7),
    ("metadata.get", 2),
    ("postgres_target.get", 3),
    ("set", 1),
    ("sorted", 7),
    ("source_edges.setdefault", 1),
    ("targets.values", 1),
    ("tuple", 17),
    ("type", 2),
    ("uses.add", 3),
    ("uses.update", 1),
    ("value.split", 1),
)

class EffectAttemptCoordinatorContractTests(
    EffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    def test_control_accepted_effect_attempt_languages_are_lawful(self) -> None:
        started = self.newly_started()
        runtime_result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        fold = self.fold_command_for(runtime_result)
        reconciled = ReconcileEffectAttempt(
            "request-a",
            started.attempt.state.identity,
            self.pinned_runtime_context().authority,
            self.pinned_runtime_context().fence,
        )

        self.assertIs(type(started), NewlyStarted)
        self.assertIs(type(fold), FoldEffectAttempt)
        self.assertIs(type(reconciled), ReconcileEffectAttempt)
        self.assertIs(type(self.exact_fold_result()), NewlyFolded)
        self.assertIs(type(self.exact_fold_result(existing=True)), ExistingFold)

    def test_control_core_intent_request_inverse_and_fingerprint_are_lawful(
        self,
    ) -> None:
        intent = self.runtime_intent()
        started = self.newly_started()
        self.assertEqual(
            runtime_effect_intent_for_request(
                self.request_for_started(intent, started)
            ),
            intent,
        )
        self.assertEqual(
            runtime_effect_intent_fingerprint(intent),
            started.attempt.state.request_fingerprint,
        )

    def test_control_legacy_ingress_and_socket_dispatch_remains_exact(self) -> None:
        runtime = RecordingCoordinatorAdapter()
        ingress = RecordingCoordinatorAdapter()
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        ingress_context = context_for(
            AllocatePublicIngress(PublicIngressActivityTarget("gateway-public"))
        )
        socket_context = context_for(
            SwitchSocketConnection(SocketConnectionTarget("edge-a"))
        )

        ingress_result = dispatcher.execute(ingress_context)
        socket_result = dispatcher.execute(socket_context)

        self.assertIs(type(ingress_result), ActivityExecutionOutcome)
        self.assertIs(type(socket_result), ActivityExecutionOutcome)
        self.assertEqual(ingress.legacy_contexts, [ingress_context])
        self.assertEqual(runtime.legacy_contexts, [socket_context])
        self.assertEqual(ingress.runtime_calls, [])
        self.assertEqual(runtime.runtime_calls, [])

    def test_control_shared_architecture_policies_are_lawful(self) -> None:
        path = "tests/effect_coordinator_policy_canary.py"
        module = "effect_coordinator_policy_canary"
        facts = architecture_testing.analyze_source(
            "from sample.effects import execute as execute_effect\n"
            "execute_effect()\n",
            path=path,
            module=module,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.canary.effect-coordinator.imports"
                    ),
                    architecture_testing.RuleId("exact"),
                    path,
                    module,
                    (
                        architecture_testing.ImportSurfaceEntry(
                            "sample.effects",
                            "execute",
                            "execute_effect",
                        ),
                    ),
                    "effect coordinator canary imports differ",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.canary.effect-coordinator.calls"
                    ),
                    architecture_testing.RuleId("exact"),
                    path,
                    module,
                    (
                        architecture_testing.ResolvedCallTarget(
                            "sample.effects.execute"
                        ),
                    ),
                    "effect coordinator canary calls differ",
                ),
            ),
        )
        self.assertEqual(findings, ())

    def test_constructor_adds_only_start_fold_and_reconciliation_services(self) -> None:
        signature = inspect.signature(ExecutionCoordinator)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "unit_of_work_factory",
                "lifecycle",
                "adapter",
                "start_service",
                "fold_service",
                "reconciliation_service",
                "clock",
                "id_factory",
            ),
        )
        for name in (
            "lifecycle",
            "adapter",
            "start_service",
            "fold_service",
            "reconciliation_service",
            "clock",
            "id_factory",
        ):
            self.assertIs(
                signature.parameters[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )
        self.assertNotIn("provider", signature.parameters)
        self.assertNotIn("observer", signature.parameters)
        start = RecordingStartService(self.newly_started())
        fold = RecordingFoldService(self.exact_fold_result())
        reconciliation = RecordingReconciliationService(
            self.exact_fold_result(existing=True)
        )
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconciliation,
            adapter=RecordingCoordinatorAdapter(),
        )
        self.assertIs(coordinator._start_service, start)
        self.assertIs(coordinator._fold_service, fold)
        self.assertIs(coordinator._reconciliation_service, reconciliation)

    def test_forward_coordinator_blocks_compensation_before_any_effect_authority(
        self,
    ) -> None:
        context = self.pinned_runtime_context()
        activity = context.plan.activity(ActivityId("activity-a"))
        compensation_event = ActivityEventRecord(
            "compensation-start-event-a",
            context.run.run_id,
            1,
            ActivityEventKind.STEP_COMPENSATION_STARTED,
            "2030-01-01T00:00:01Z",
            activity_id=activity.activity_id.value,
        )
        shared_realization = context.realization_context(
            activity,
            compensation_event,
        )
        self.assertEqual(shared_realization.intent_event, compensation_event)

        start = RecordingStartService()
        fold = RecordingFoldService()
        reconciliation = RecordingReconciliationService()
        adapter = RecordingCoordinatorAdapter()
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconciliation,
            adapter=adapter,
        )
        coordinator.pinned_context = replace(
            context,
            run=replace(context.run, status=ActivityRunStatus.COMPENSATING),
            events=(compensation_event,),
        )

        result = coordinator.execute(self.coordinator_command())

        self.assertIs(result.status, CoordinatorStatus.BLOCKED)
        self.assertEqual(result.effects_attempted, 0)
        self.assertEqual(start.commands, [])
        self.assertEqual(reconciliation.commands, [])
        self.assertEqual(fold.commands, [])
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(adapter.legacy_contexts, [])
        self.assertEqual(coordinator.legacy_writes, [])
        self.assertEqual(coordinator.effect_ledger, [])
        forward_source = inspect.getsource(ExecutionCoordinator.execute)
        self.assertNotIn("STEP_COMPENSATION_STARTED", forward_source)
        self.assertNotIn("FailedRunCompensation", forward_source)
        self.assertNotIn("failed_run_compensation", forward_source)

    def test_adapter_protocol_and_dispatchers_expose_exact_operation_indexed_arms(
        self,
    ) -> None:
        self.assertEqual(ActivityExecutionAdapter.__bases__, (Protocol,))
        self.assertIs(ActivityExecutionAdapter._is_protocol, True)
        self.assertEqual(
            tuple(inspect.signature(ActivityExecutionAdapter.execute).parameters),
            ("self", "context"),
        )
        adapter_execute_runtime = getattr(
            ActivityExecutionAdapter,
            "execute_runtime",
            None,
        )
        activity_execute_runtime = getattr(
            ActivityExecutionDispatcher,
            "execute_runtime",
            None,
        )
        runtime_execute_runtime = getattr(
            RuntimeInterpreterDispatcher,
            "execute_runtime",
            None,
        )
        self.assertIsNotNone(adapter_execute_runtime)
        self.assertIsNotNone(activity_execute_runtime)
        self.assertIsNotNone(runtime_execute_runtime)
        self.assertEqual(
            tuple(
                inspect.signature(adapter_execute_runtime).parameters
            ),
            ("self", "context", "request"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(activity_execute_runtime).parameters
            ),
            ("self", "context", "request"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(runtime_execute_runtime).parameters
            ),
            ("self", "context", "request"),
        )
        self.assertIs(
            get_type_hints(ActivityExecutionAdapter.execute)["return"],
            ActivityExecutionOutcome,
        )
        self.assertIs(
            get_type_hints(adapter_execute_runtime)["return"],
            RuntimeEffectResult,
        )

        runtime_context = context_for(self.runtime_intent().operation)
        activity_error = None
        try:
            ActivityExecutionDispatcher(
                runtime=RecordingCoordinatorAdapter()
            ).execute(runtime_context)
        except BaseException as error:
            activity_error = error
        interpreter_error = None
        try:
            RuntimeInterpreterDispatcher({}).execute(runtime_context)
        except BaseException as error:
            interpreter_error = error

        self.assertIs(type(activity_error), InvalidOperationCommand)
        self.assertIs(type(interpreter_error), InvalidOperationCommand)
        self.assertIsNot(activity_error, interpreter_error)
        for error in (activity_error, interpreter_error):
            self.assertEqual(
                error.args,
                ("runtime activities require the runtime dispatch arm",),
            )
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)

    def test_live_start_binds_exact_event_request_and_folds_once(self) -> None:
        started = self.newly_started()
        result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id,
            evidence={"provider": "docker"},
        )
        start = RecordingStartService(started)
        fold = RecordingFoldService(
            lambda command: self.fold_result_for(command, started)
        )
        reconcile = RecordingReconciliationService()
        adapter = RecordingCoordinatorAdapter(result)
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconcile,
            adapter=adapter,
        )

        outcome = coordinator.execute(self.coordinator_command())

        self.assertEqual(len(start.commands), 1)
        start_command = start.commands[0]
        self.assertIs(type(start_command), StartEffectAttempt)
        self.assertEqual(start_command.intent, self.runtime_intent())
        self.assertEqual(
            start_command.transition.request_fingerprint,
            runtime_effect_intent_fingerprint(start_command.intent),
        )
        self.assertEqual(len(adapter.runtime_calls), 1)
        context, request = adapter.runtime_calls[0]
        self.assertEqual(
            request.effect_id,
            started.attempt.original_start_event.event_id,
        )
        self.assertEqual(
            request.source.intent_event_id,
            started.attempt.original_start_event.event_id,
        )
        self.assertEqual(
            runtime_effect_intent_for_request(request),
            start_command.intent,
        )
        self.assertEqual(context.intent_event, started.attempt.original_start_event)
        self.assertEqual(len(fold.commands), 1)
        folded = fold.commands[0]
        self.assertEqual(folded.outcome.result, result)
        self.assertEqual(folded.transition, effect_outcome_transition(folded.outcome))
        self.assertEqual(folded.failure, effect_outcome_failure(folded.outcome))
        self.assertEqual(reconcile.commands, [])
        self.assertEqual(adapter.legacy_contexts, [])
        self.assertEqual(coordinator.legacy_writes, [])
        self.assertEqual(coordinator.effect_ledger, [])
        self.assertEqual(outcome.effects_attempted, 1)

    def test_existing_attempt_reconciles_without_provider_or_direct_fold(self) -> None:
        existing = ExistingAttempt(self.direct_attempt())
        start = RecordingStartService(existing)
        fold = RecordingFoldService()
        reconcile = RecordingReconciliationService(
            self.exact_fold_result(existing=True)
        )
        adapter = RecordingCoordinatorAdapter()
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconcile,
            adapter=adapter,
        )

        outcome = coordinator.execute(self.coordinator_command())

        self.assertEqual(len(start.commands), 1)
        self.assertEqual(len(reconcile.commands), 1)
        command = reconcile.commands[0]
        self.assertEqual(command.request_id, "request-a")
        self.assertEqual(command.identity, existing.attempt.state.identity)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(fold.commands, [])
        self.assertEqual(coordinator.legacy_writes, [])
        self.assertEqual(coordinator.effect_ledger, [])
        self.assertEqual(outcome.effects_attempted, 1)

    def test_start_results_must_match_the_exact_issued_command_before_effects(
        self,
    ) -> None:
        candidates = (
            (
                "identity",
                self.started_attempt(identity=self.identity(run_id="run-b")),
            ),
            (
                "fingerprint",
                self.started_attempt(request_fingerprint="b" * 64),
            ),
            (
                "fence",
                self.started_attempt(fence=EffectAttemptFence("worker-b", 8)),
            ),
            (
                "original-event",
                self.forged_original_event_attempt(),
            ),
        )
        for result_type in (NewlyStarted, ExistingAttempt):
            for drift, attempt in candidates:
                with self.subTest(
                    result=result_type.__name__,
                    drift=drift,
                ):
                    start = RecordingStartService(result_type(attempt))
                    fold = RecordingFoldService()
                    reconciliation = RecordingReconciliationService()
                    adapter = RecordingCoordinatorAdapter()
                    coordinator = self.db_free_coordinator(
                        start_service=start,
                        fold_service=fold,
                        reconciliation_service=reconciliation,
                        adapter=adapter,
                    )

                    with self.assertRaises(ExecutionCoordinatorConflict) as caught:
                        coordinator.execute(self.coordinator_command())

                    self.assertEqual(
                        str(caught.exception),
                        (
                            "effect attempt service result is invalid"
                            if drift == "original-event"
                            else "effect attempt start result is invalid"
                        ),
                    )
                    self.assert_safe_error(caught.exception)
                    self.assertEqual(len(start.commands), 1)
                    issued = start.commands[0]
                    self.assertEqual(issued.intent, self.runtime_intent())
                    self.assertEqual(
                        issued.transition.request_fingerprint,
                        runtime_effect_intent_fingerprint(issued.intent),
                    )
                    self.assertEqual(adapter.runtime_calls, [])
                    self.assertEqual(fold.commands, [])
                    self.assertEqual(reconciliation.commands, [])
                    self.assertEqual(coordinator.legacy_writes, [])
                    self.assertEqual(coordinator.effect_ledger, [])

    def test_adapter_fault_and_wrong_arm_become_one_direct_uncertain_fold(self) -> None:
        started = self.newly_started()
        cases = (
            ("raised", RuntimeError("provider-secret-canary")),
            ("wrong-arm", ActivityExecutionOutcome.succeeded()),
        )
        for label, adapter_value in cases:
            with self.subTest(case=label):
                fold = RecordingFoldService(
                    lambda command: self.fold_result_for(command, started)
                )
                coordinator = self.db_free_coordinator(
                    start_service=RecordingStartService(started),
                    fold_service=fold,
                    reconciliation_service=RecordingReconciliationService(),
                    adapter=RecordingCoordinatorAdapter(adapter_value),
                )

                outcome = coordinator.execute(self.coordinator_command())

                self.assertEqual(outcome.effects_attempted, 1)
                self.assertEqual(len(fold.commands), 1)
                result = fold.commands[0].outcome.result
                self.assertIs(type(result), RuntimeEffectResult)
                self.assertIs(result.kind, EffectResultKind.UNCERTAIN)
                self.assertEqual(
                    result.effect_id,
                    started.attempt.original_start_event.event_id,
                )
                self.assertIsNotNone(result.failure)
                assert result.failure is not None
                self.assertEqual(result.failure.code, "runtime.provider-result-unknown")
                self.assertNotIn("provider-secret-canary", repr(result))
                self.assertEqual(coordinator.legacy_writes, [])
                self.assertEqual(coordinator.effect_ledger, [])

    def test_recovery_truth_conflicts_before_provider_or_reconciliation(self) -> None:
        recovered = self.recovery_attempt()
        start = RecordingStartService(ExistingAttempt(recovered))
        fold = RecordingFoldService()
        reconcile = RecordingReconciliationService()
        adapter = RecordingCoordinatorAdapter()
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconcile,
            adapter=adapter,
        )

        with self.assertRaises(ExecutionCoordinatorConflict) as caught:
            coordinator.execute(self.coordinator_command())

        self.assertEqual(
            str(caught.exception),
            "effect attempt recovery requires explicit recovery authority",
        )
        self.assert_safe_error(caught.exception)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(reconcile.commands, [])
        self.assertEqual(fold.commands, [])
        self.assertEqual(coordinator.legacy_writes, [])
        self.assertEqual(coordinator.effect_ledger, [])

    def test_selected_iterations_consume_budget_independently_of_provider_calls(
        self,
    ) -> None:
        started = self.newly_started()
        terminal = ExistingAttempt(self.direct_attempt())
        result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        start = RecordingStartService(started, terminal)
        fold = RecordingFoldService(
            lambda command: self.fold_result_for(command, started)
        )
        reconcile = RecordingReconciliationService(
            self.exact_fold_result(existing=True)
        )
        adapter = RecordingCoordinatorAdapter(result)
        coordinator = self.db_free_coordinator(
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconcile,
            adapter=adapter,
        )

        outcome = coordinator.execute(self.coordinator_command(max_effects=2))

        self.assertEqual(len(start.commands), 2)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(len(reconcile.commands), 1)
        self.assertEqual(outcome.effects_attempted, 2)
        self.assertEqual(coordinator.legacy_writes, [])
        self.assertEqual(coordinator.effect_ledger, [])

    def test_named_service_errors_are_bounded_and_unexpected_faults_remain_raw(
        self,
    ) -> None:
        cases = (
            (
                "start-not-found",
                RecordingStartService(EffectAttemptStartNotFound("candidate-a")),
                ExecutionCoordinatorNotFound,
                "effect attempt start truth was not found",
            ),
            (
                "start-conflict",
                RecordingStartService(EffectAttemptStartConflict("candidate-b")),
                ExecutionCoordinatorConflict,
                "effect attempt start truth is invalid",
            ),
            (
                "start-denied",
                RecordingStartService(EffectAttemptStartDenied("candidate-c")),
                ExecutionCoordinatorDenied,
                "effect attempt start authority is invalid",
            ),
        )
        for label, start, error_type, message in cases:
            with self.subTest(case=label):
                coordinator = self.db_free_coordinator(
                    start_service=start,
                    fold_service=RecordingFoldService(),
                    reconciliation_service=RecordingReconciliationService(),
                    adapter=RecordingCoordinatorAdapter(),
                )
                with self.assertRaises(error_type) as caught:
                    coordinator.execute(self.coordinator_command())
                self.assertEqual(str(caught.exception), message)
                self.assert_safe_error(caught.exception, "candidate")

        with self.subTest(case="raw-start"):
            sentinel = RuntimeError("internal-service-canary")
            coordinator = self.db_free_coordinator(
                start_service=RecordingStartService(sentinel),
                fold_service=RecordingFoldService(),
                reconciliation_service=RecordingReconciliationService(),
                adapter=RecordingCoordinatorAdapter(),
            )
            with self.assertRaises(RuntimeError) as caught:
                coordinator.execute(self.coordinator_command())
            self.assertIs(caught.exception, sentinel)

    def test_fold_and_reconciliation_errors_have_exact_coordinator_categories(
        self,
    ) -> None:
        started = self.newly_started()
        runtime_result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        service_errors = (
            (EffectAttemptFoldNotFound("fold-canary"), ExecutionCoordinatorNotFound),
            (EffectAttemptFoldConflict("fold-canary"), ExecutionCoordinatorConflict),
            (EffectAttemptFoldDenied("fold-canary"), ExecutionCoordinatorDenied),
        )
        for service_error, expected in service_errors:
            with self.subTest(boundary=type(service_error).__name__):
                coordinator = self.db_free_coordinator(
                    start_service=RecordingStartService(started),
                    fold_service=RecordingFoldService(service_error),
                    reconciliation_service=RecordingReconciliationService(),
                    adapter=RecordingCoordinatorAdapter(runtime_result),
                )
                with self.assertRaises(expected) as caught:
                    coordinator.execute(self.coordinator_command())
                expected_message = {
                    EffectAttemptFoldNotFound: (
                        "effect attempt fold truth was not found"
                    ),
                    EffectAttemptFoldConflict: "effect attempt fold truth is invalid",
                    EffectAttemptFoldDenied: "effect attempt fold authority is invalid",
                }[type(service_error)]
                self.assertEqual(str(caught.exception), expected_message)
                self.assert_safe_error(caught.exception, "fold-canary")

        existing = ExistingAttempt(self.direct_attempt())
        reconcile_errors = (
            (
                EffectAttemptReconciliationNotFound("reconcile-canary"),
                ExecutionCoordinatorNotFound,
            ),
            (
                EffectAttemptReconciliationConflict("reconcile-canary"),
                ExecutionCoordinatorConflict,
            ),
            (
                EffectAttemptReconciliationDenied("reconcile-canary"),
                ExecutionCoordinatorDenied,
            ),
        )
        for service_error, expected in reconcile_errors:
            with self.subTest(boundary=type(service_error).__name__):
                coordinator = self.db_free_coordinator(
                    start_service=RecordingStartService(existing),
                    fold_service=RecordingFoldService(),
                    reconciliation_service=RecordingReconciliationService(
                        service_error
                    ),
                    adapter=RecordingCoordinatorAdapter(),
                )
                with self.assertRaises(expected) as caught:
                    coordinator.execute(self.coordinator_command())
                expected_message = {
                    EffectAttemptReconciliationNotFound: (
                        "effect attempt reconciliation truth was not found"
                    ),
                    EffectAttemptReconciliationConflict: (
                        "effect attempt reconciliation truth is invalid"
                    ),
                    EffectAttemptReconciliationDenied: (
                        "effect attempt reconciliation authority is invalid"
                    ),
                }[type(service_error)]
                self.assertEqual(str(caught.exception), expected_message)
                self.assert_safe_error(caught.exception, "reconcile-canary")

        with self.subTest(boundary="raw-fold"):
            fold_sentinel = RuntimeError("raw-fold-canary")
            coordinator = self.db_free_coordinator(
                start_service=RecordingStartService(started),
                fold_service=RecordingFoldService(fold_sentinel),
                reconciliation_service=RecordingReconciliationService(),
                adapter=RecordingCoordinatorAdapter(runtime_result),
            )
            with self.assertRaises(RuntimeError) as caught:
                coordinator.execute(self.coordinator_command())
            self.assertIs(caught.exception, fold_sentinel)

        with self.subTest(boundary="raw-reconciliation"):
            reconcile_sentinel = RuntimeError("raw-reconcile-canary")
            coordinator = self.db_free_coordinator(
                start_service=RecordingStartService(existing),
                fold_service=RecordingFoldService(),
                reconciliation_service=RecordingReconciliationService(
                    reconcile_sentinel
                ),
                adapter=RecordingCoordinatorAdapter(),
            )
            with self.assertRaises(RuntimeError) as caught:
                coordinator.execute(self.coordinator_command())
            self.assertIs(caught.exception, reconcile_sentinel)

    def test_service_results_are_exact_before_virtual_or_equality_dispatch(
        self,
    ) -> None:
        dispatches: list[str] = []

        def hostile_copy(value, label: str):
            class HostileResult(type(value)):
                def __getattribute__(self, name):
                    dispatches.append(f"{label}.{name}")
                    raise AssertionError("hostile result dispatched")

                def __eq__(self, _other):
                    dispatches.append(f"{label}.eq")
                    raise AssertionError("hostile result equality dispatched")

            hostile = object.__new__(HostileResult)
            for item in fields(type(value)):
                object.__setattr__(hostile, item.name, getattr(value, item.name))
            return hostile

        def forged_attempt(
            lawful: EffectAttemptRecord,
            *,
            field_name: str,
            label: str,
        ) -> EffectAttemptRecord:
            candidate = object.__new__(EffectAttemptRecord)
            for item in fields(EffectAttemptRecord):
                value = getattr(lawful, item.name)
                if item.name == field_name:
                    value = hostile_copy(value, label)
                object.__setattr__(candidate, item.name, value)
            return candidate

        def exact_start_result(result_type, attempt):
            candidate = object.__new__(result_type)
            object.__setattr__(candidate, "attempt", attempt)
            return candidate

        def exact_fold_result(result_type, lawful, attempt):
            candidate = object.__new__(result_type)
            object.__setattr__(candidate, "attempt", attempt)
            object.__setattr__(
                candidate,
                "outcome_record",
                lawful.outcome_record,
            )
            return candidate

        def capture(coordinator):
            escaped = None
            try:
                coordinator.execute(self.coordinator_command())
            except BaseException as error:
                escaped = error
            return escaped

        class UnrelatedAttempt:
            def __getattribute__(self, name):
                dispatches.append(f"unrelated.{name}")
                raise AssertionError("unrelated attempt dispatched")

            def __eq__(self, _other):
                dispatches.append("unrelated.eq")
                raise AssertionError("unrelated attempt equality dispatched")

        started = self.newly_started()
        runtime_result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        lawful_new_fold = self.fold_result_for(
            self.fold_command_for(runtime_result),
            started,
        )
        existing = ExistingAttempt(self.direct_attempt())
        lawful_existing_fold = self.exact_fold_result(existing=True)

        start_cases = [
            (
                "outer",
                hostile_copy(started, "start.outer"),
            ),
        ]
        for result_type, lawful_attempt in (
            (NewlyStarted, started.attempt),
            (ExistingAttempt, existing.attempt),
        ):
            start_cases.extend(
                (
                    (
                        f"{result_type.__name__}-unrelated",
                        exact_start_result(result_type, UnrelatedAttempt()),
                    ),
                    (
                        f"{result_type.__name__}-deep-state",
                        exact_start_result(
                            result_type,
                            forged_attempt(
                                lawful_attempt,
                                field_name="state",
                                label=f"{result_type.__name__}.state",
                            ),
                        ),
                    ),
                    (
                        f"{result_type.__name__}-deep-event",
                        exact_start_result(
                            result_type,
                            forged_attempt(
                                lawful_attempt,
                                field_name="original_start_event",
                                label=f"{result_type.__name__}.event",
                            ),
                        ),
                    ),
                )
            )

        for label, start_result in start_cases:
            with self.subTest(boundary="start", candidate=label):
                dispatches.clear()
                start = RecordingStartService(start_result)
                fold = RecordingFoldService()
                reconciliation = RecordingReconciliationService()
                adapter = RecordingCoordinatorAdapter()
                coordinator = self.db_free_coordinator(
                    start_service=start,
                    fold_service=fold,
                    reconciliation_service=reconciliation,
                    adapter=adapter,
                )
                escaped = capture(coordinator)
                self.assertEqual(dispatches, [])
                self.assertIs(type(escaped), ExecutionCoordinatorConflict)
                self.assertEqual(
                    str(escaped),
                    "effect attempt service result is invalid",
                )
                self.assert_safe_error(escaped)
                self.assertEqual(adapter.runtime_calls, [])
                self.assertEqual(fold.commands, [])
                self.assertEqual(reconciliation.commands, [])
                self.assertEqual(coordinator.legacy_writes, [])

        fold_cases = [
            ("outer", hostile_copy(lawful_new_fold, "fold.outer")),
        ]
        reconciliation_cases = [
            (
                "outer",
                hostile_copy(lawful_existing_fold, "reconciliation.outer"),
            ),
        ]
        for result_type, lawful in (
            (NewlyFolded, lawful_new_fold),
            (ExistingFold, lawful_existing_fold),
        ):
            fold_cases.append(
                (
                    f"{result_type.__name__}-deep",
                    exact_fold_result(
                        result_type,
                        lawful,
                        forged_attempt(
                            lawful.attempt,
                            field_name="state",
                            label=f"fold.{result_type.__name__}.state",
                        ),
                    ),
                )
            )
            reconciliation_cases.append(
                (
                    f"{result_type.__name__}-deep",
                    exact_fold_result(
                        result_type,
                        lawful,
                        forged_attempt(
                            lawful.attempt,
                            field_name="state",
                            label=(
                                f"reconciliation.{result_type.__name__}.state"
                            ),
                        ),
                    ),
                )
            )

        for label, fold_result in fold_cases:
            with self.subTest(boundary="fold", candidate=label):
                dispatches.clear()
                fold = RecordingFoldService(fold_result)
                reconciliation = RecordingReconciliationService()
                adapter = RecordingCoordinatorAdapter(runtime_result)
                coordinator = self.db_free_coordinator(
                    start_service=RecordingStartService(started),
                    fold_service=fold,
                    reconciliation_service=reconciliation,
                    adapter=adapter,
                )
                escaped = capture(coordinator)
                self.assertEqual(dispatches, [])
                self.assertIs(type(escaped), ExecutionCoordinatorConflict)
                self.assertEqual(
                    str(escaped),
                    "effect attempt service result is invalid",
                )
                self.assert_safe_error(escaped)
                self.assertEqual(len(adapter.runtime_calls), 1)
                self.assertEqual(len(fold.commands), 1)
                self.assertEqual(reconciliation.commands, [])
                self.assertEqual(coordinator.legacy_writes, [])

        for label, reconciliation_result in reconciliation_cases:
            with self.subTest(boundary="reconciliation", candidate=label):
                dispatches.clear()
                fold = RecordingFoldService()
                reconciliation = RecordingReconciliationService(
                    reconciliation_result
                )
                adapter = RecordingCoordinatorAdapter()
                coordinator = self.db_free_coordinator(
                    start_service=RecordingStartService(existing),
                    fold_service=fold,
                    reconciliation_service=reconciliation,
                    adapter=adapter,
                )
                escaped = capture(coordinator)
                self.assertEqual(dispatches, [])
                self.assertIs(type(escaped), ExecutionCoordinatorConflict)
                self.assertEqual(
                    str(escaped),
                    "effect attempt service result is invalid",
                )
                self.assert_safe_error(escaped)
                self.assertEqual(adapter.runtime_calls, [])
                self.assertEqual(fold.commands, [])
                self.assertEqual(len(reconciliation.commands), 1)
                self.assertEqual(coordinator.legacy_writes, [])

    def test_fold_and_reconciliation_results_match_the_issued_attempt(
        self,
    ) -> None:
        started = self.newly_started()
        existing = ExistingAttempt(self.direct_attempt())
        runtime_result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )

        for drift in ("identity", "fingerprint"):
            for result_type in (NewlyFolded, ExistingFold):
                foreign = self.lawful_foreign_fold_result(
                    drift=drift,
                    existing=result_type is ExistingFold,
                )
                with self.subTest(
                    boundary="fold",
                    drift=drift,
                    result=result_type.__name__,
                ):
                    fold = RecordingFoldService(foreign)
                    reconciliation = RecordingReconciliationService()
                    adapter = RecordingCoordinatorAdapter(runtime_result)
                    coordinator = self.db_free_coordinator(
                        start_service=RecordingStartService(started),
                        fold_service=fold,
                        reconciliation_service=reconciliation,
                        adapter=adapter,
                    )

                    with self.assertRaises(ExecutionCoordinatorConflict) as caught:
                        coordinator.execute(self.coordinator_command())

                    self.assertEqual(
                        str(caught.exception),
                        "effect attempt service result is invalid",
                    )
                    self.assert_safe_error(caught.exception)
                    self.assertEqual(len(adapter.runtime_calls), 1)
                    self.assertEqual(len(fold.commands), 1)
                    issued = fold.commands[0]
                    self.assertTrue(
                        foreign.attempt.state.identity != issued.transition.identity
                        or foreign.attempt.state.request_fingerprint
                        != issued.outcome.request_fingerprint
                    )
                    self.assertEqual(reconciliation.commands, [])
                    self.assertEqual(coordinator.legacy_writes, [])

                with self.subTest(
                    boundary="reconciliation",
                    drift=drift,
                    result=result_type.__name__,
                ):
                    fold = RecordingFoldService()
                    reconciliation = RecordingReconciliationService(foreign)
                    adapter = RecordingCoordinatorAdapter()
                    coordinator = self.db_free_coordinator(
                        start_service=RecordingStartService(existing),
                        fold_service=fold,
                        reconciliation_service=reconciliation,
                        adapter=adapter,
                    )

                    with self.assertRaises(ExecutionCoordinatorConflict) as caught:
                        coordinator.execute(self.coordinator_command())

                    self.assertEqual(
                        str(caught.exception),
                        "effect attempt service result is invalid",
                    )
                    self.assert_safe_error(caught.exception)
                    self.assertEqual(adapter.runtime_calls, [])
                    self.assertEqual(fold.commands, [])
                    self.assertEqual(len(reconciliation.commands), 1)
                    issued = reconciliation.commands[0]
                    self.assertTrue(
                        foreign.attempt.state.identity != issued.identity
                        or foreign.attempt.state.request_fingerprint
                        != existing.attempt.state.request_fingerprint
                    )
                    self.assertEqual(coordinator.legacy_writes, [])

    def test_root_inventory_and_private_projection_ownership_are_exact(self) -> None:
        self.assertEqual(
            COORDINATOR_EXPORTS.difference(operations_root.__all__),
            set(),
        )
        self.assertNotIn(
            "_runtime_effect_intent_for_context",
            operations_root.__all__,
        )
        self.assertFalse(
            hasattr(operations_root, "_runtime_effect_intent_for_context")
        )
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = {
            row["module"]: row
            for row in inventory["modules"]
            if row["module"] in {COORDINATOR_MODULE, RUNTIME_EFFECTS_MODULE}
        }
        self.assertEqual(set(entries), {COORDINATOR_MODULE, RUNTIME_EFFECTS_MODULE})
        coordinator = entries[COORDINATOR_MODULE]
        runtime_effects = entries[RUNTIME_EFFECTS_MODULE]
        self.assertEqual(
            set(coordinator["canonical_public_exports"]),
            COORDINATOR_EXPORTS,
        )
        self.assertEqual(
            set(coordinator["internal_dependencies"]),
            COORDINATOR_DEPENDENCIES,
        )
        self.assertEqual(
            set(coordinator["protecting_tests"]),
            {
                "tests/test_effect_attempt_coordinator_contract.py",
                "tests/test_execution_coordinator.py",
                "tests/test_postgres_effect_attempt_coordinator_budget_lifecycle.py",
                "tests/test_postgres_effect_attempt_coordinator_compensation_isolation.py",
                "tests/test_postgres_effect_attempt_coordinator_concurrency.py",
                "tests/test_postgres_effect_attempt_coordinator_crash_rollback.py",
                "tests/test_postgres_effect_attempt_coordinator_first_replay.py",
            },
        )
        self.assertEqual(
            runtime_effects["canonical_public_exports"],
            ["runtime_effect_request_for_context"],
        )
        self.assertEqual(
            set(runtime_effects["internal_dependencies"]),
            RUNTIME_EFFECTS_DEPENDENCIES,
        )
        self.assertEqual(
            set(runtime_effects["protecting_tests"]),
            {"tests/test_runtime_effect_translation.py"},
        )

    def test_coordinator_effect_attempt_imports_and_calls_are_closed(self) -> None:
        facts = architecture_testing.analyze_source(
            COORDINATOR_SOURCE.read_text(encoding="utf-8"),
            path=COORDINATOR_SOURCE_PATH,
            module=COORDINATOR_MODULE,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.effect-coordinator.imports"
                    ),
                    architecture_testing.RuleId("exact"),
                    COORDINATOR_SOURCE_PATH,
                    COORDINATOR_MODULE,
                    EXACT_COORDINATOR_IMPORTS,
                    "effect coordinator import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.effect-coordinator.calls"
                    ),
                    architecture_testing.RuleId("exact"),
                    COORDINATOR_SOURCE_PATH,
                    COORDINATOR_MODULE,
                    EXACT_COORDINATOR_CALLS,
                    "effect coordinator call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())

    def test_runtime_effect_private_intent_projection_calls_only_public_core_algebra(
        self,
    ) -> None:
        facts = architecture_testing.analyze_source(
            RUNTIME_EFFECTS_SOURCE.read_text(encoding="utf-8"),
            path=RUNTIME_EFFECTS_SOURCE_PATH,
            module=RUNTIME_EFFECTS_MODULE,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.runtime-intent.imports"
                    ),
                    architecture_testing.RuleId("exact"),
                    RUNTIME_EFFECTS_SOURCE_PATH,
                    RUNTIME_EFFECTS_MODULE,
                    EXACT_RUNTIME_EFFECTS_IMPORTS,
                    "runtime effect translation import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.runtime-intent.calls"
                    ),
                    architecture_testing.RuleId("exact"),
                    RUNTIME_EFFECTS_SOURCE_PATH,
                    RUNTIME_EFFECTS_MODULE,
                    EXACT_RUNTIME_EFFECTS_CALLS,
                    "runtime effect translation call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())

    @staticmethod
    def request_for_started(intent, started: NewlyStarted):
        from control_plane_kit_core.runtime_effect_observation import (
            runtime_effect_request_for_intent,
        )

        return runtime_effect_request_for_intent(
            intent,
            effect_id=started.attempt.original_start_event.event_id,
            secret_resolution_grants=(),
        )


if __name__ == "__main__":
    unittest.main()
