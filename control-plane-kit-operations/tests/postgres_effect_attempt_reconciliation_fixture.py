from __future__ import annotations

from dataclasses import replace
from unittest import mock

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest,
    runtime_effect_intent_fingerprint,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import (
    EffectAttemptReconciliationService,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.runtime_effects import (
    required_secret_uses_for_runtime_effect,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
    secret_use_correlation_for,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_guarded_observed_effect_fold_fixture import (
    PostgresGuardedObservedEffectFoldFixture,
)
from tests.runtime_effect_reconciliation_fixture import ReconcileEffectAttempt


AUTHORITY_ERROR = "effect attempt reconciliation authority is invalid"
INVALID_TRUTH_ERROR = "effect attempt reconciliation truth is invalid"
NOT_FOUND_ERROR = "effect attempt reconciliation truth was not found"
REPLAY_ERROR = "effect attempt reconciliation is incongruent"


class UnitOfWorkLedger:
    """Track every transaction lifetime without changing its behavior."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self.active = 0
        self.entries = 0
        self.exits = 0

    def __call__(self):
        return _TrackedUnitOfWork(self, self._factory())


class _TrackedUnitOfWork:
    def __init__(self, ledger: UnitOfWorkLedger, unit_of_work) -> None:
        self._ledger = ledger
        self._unit_of_work = unit_of_work

    def __enter__(self):
        entered = self._unit_of_work.__enter__()
        self._ledger.active += 1
        self._ledger.entries += 1
        return entered

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._unit_of_work.__exit__(exc_type, exc, traceback)
        finally:
            self._ledger.active -= 1
            self._ledger.exits += 1


class RecordingObserver:
    def __init__(self, result, *, ledger=None, error=None) -> None:
        self.result = result
        self.ledger = ledger
        self.error = error
        self.calls = []

    def observe(self, request, authority):
        if self.ledger is not None and self.ledger.active:
            raise AssertionError("runtime observer ran inside a unit of work")
        self.calls.append((request, authority))
        if self.error is not None:
            raise self.error
        return self.result


class FailIfObserver:
    def __init__(self, message="runtime observer was invoked") -> None:
        self.error = AssertionError(message)
        self.calls = []

    def observe(self, request, authority):
        self.calls.append((request, authority))
        raise self.error


class FailIfFold:
    def __init__(self, message="atomic fold was invoked") -> None:
        self.error = AssertionError(message)
        self.calls = []

    def execute_observed(self, command):
        self.calls.append(command)
        raise self.error


class PostgresEffectAttemptReconciliationFixture(
    PostgresGuardedObservedEffectFoldFixture,
):
    """Accepted #1707/#1708 truth for the #1694 transaction program."""

    def reconciliation_command(
        self,
        current=None,
        *,
        worker_id="worker-a",
        generation=7,
        scopes=(PolicyScope.EXECUTION_OPERATE,),
    ):
        current = current or self.current_attempt()
        return ReconcileEffectAttempt(
            "request-a",
            current.state.identity,
            self.authority(worker_id, scopes),
            self.fence(worker_id, generation),
        )

    def observation_for(self, story, current, intent):
        return replace(
            story.value,
            effect_id=current.original_start_event.event_id,
            request_fingerprint=runtime_effect_intent_fingerprint(intent),
        )

    def observer_for(self, story, current, intent, *, ledger=None, error=None):
        return RecordingObserver(
            self.observation_for(story, current, intent),
            ledger=ledger,
            error=error,
        )

    def reconciliation_service(
        self,
        observer,
        story=None,
        *,
        ledger=None,
        fold_service=None,
        ids=None,
    ):
        story = story or self.observed_story()
        factory = ledger or self.unit_of_work
        if fold_service is None:
            values = ids or self.fold_ids_for_story(
                f"reconcile-{story.name}-{int(story.compensation)}",
                story,
            )
            fold_service = EffectAttemptFoldService(
                factory,
                id_factory=Sequence(*values),
            )
        return EffectAttemptReconciliationService(
            factory,
            observer,
            fold_service,
        )

    def seed_reconciliation_source(
        self,
        story=None,
        *,
        authority_ref=True,
        remote=False,
        zero_use=False,
    ):
        story = story or self.observed_story()
        if zero_use:
            self._fold_compensation = story.compensation
            self._fold_outcome_story = story
            self.reset_start_truth(compensation=story.compensation)
            current = self.record(
                "started",
                compensation=story.compensation,
                run_id="run-a",
                activity_id="start-runtime",
                event_prefix=f"reconcile-{int(story.compensation)}",
                original_ordinal=7 if story.compensation else 3,
                original_time="2030-01-01T00:00:00Z",
            )
            intent = replace(
                self.persisted_intent(current, authority_ref=False),
                products=(),
            )
            state = replace(
                current.state,
                request_fingerprint=runtime_effect_intent_fingerprint(intent),
            )
            event = replace(
                current.original_start_event,
                evidence=self.evidence_for(state),
            )
            current = EffectAttemptRecord(state, event, event)
            self.persist(current, intent=intent)
            return current, intent, self.intent_record(current, intent=intent), None
        current, intent, record = self.seed_guarded_source(
            story,
            authority_ref=authority_ref,
        )
        authority = (
            None
            if intent.authority_ref is None
            else self.register_runtime_authority(intent, remote=remote)
        )
        return current, intent, record, authority

    def expected_observed_fold(self, story, current, intent, authority):
        observation = self.observation_for(story, current, intent)
        outcome = ObservedEffectOutcome(current.state.identity, observation)
        fold = self.fold_command(
            story,
            request_id=intent.source.request_id,
            transition=effect_outcome_transition(outcome),
            authority=self.authority(),
            fence=self.fence(),
            failure=effect_outcome_failure(outcome),
            outcome=outcome,
        )
        return self.guarded_observed_command(
            story,
            current=current,
            intent=intent,
            intent_record=self.intent_record(current, intent=intent),
            runtime_authority=authority,
            fold=fold,
            register=False,
        )

    def runtime_request(self, current, intent, *, grants=()):
        return runtime_effect_request_for_intent(
            intent,
            effect_id=current.original_start_event.event_id,
            secret_resolution_grants=grants,
        )

    def required_secret_uses(self, current, intent, authority):
        return required_secret_uses_for_runtime_effect(
            self.runtime_request(current, intent),
            authority,
        )

    def admit_secret_uses(self, uses) -> None:
        intents = tuple(sorted({intent for _reference, intent in uses}, key=lambda x: x.value))
        prefixes = tuple(
            sorted(
                {
                    SecretReference(reference.reference_id.rsplit("/", 1)[0])
                    for reference, _intent in uses
                },
                key=lambda value: value.reference_id,
            )
        )
        service = SecretProviderRegistrationService(self.unit_of_work)
        provider = service.register_provider(
            RegisterSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("local"),
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                display_name="Local reconciliation secrets",
                endpoint_reference=SecretProviderEndpointReference("local-secrets"),
                credential_reference=SecretReference(
                    "secret://bootstrap/local/client-token"
                ),
                allowed_reference_prefixes=prefixes,
                allowed_intents=intents,
                admitted_by="operator-a",
                admitted_at="2030-01-01T00:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                metadata={"purpose": "reconciliation-test"},
            )
        )
        for position, (reference, intent) in enumerate(uses, start=1):
            service.register_reference(
                RegisterSecretReferenceCommand(
                    workspace_id="workspace-a",
                    reference=reference,
                    provider_registration_id=provider.registration_id,
                    allowed_intents=(intent,),
                    admitted_by="operator-a",
                    admitted_at=f"2030-01-01T00:00:{position:02d}Z",
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                    metadata={"position": position},
                )
            )

    def authorization_command(
        self,
        current,
        intent,
        reference,
        use_intent,
        *,
        command,
        requested_at="2030-01-01T00:00:02Z",
    ):
        values = {
            "workspace_id": intent.source.workspace_id,
            "reference": reference,
            "intent": use_intent,
            "actor_subject": command.authority.worker_id,
            "operation_id": intent.source.request_id,
            "run_id": current.state.identity.run_id.value,
            "activity_id": current.state.identity.activity_id,
            "effect_id": current.original_start_event.event_id,
        }
        return AuthorizeSecretUse(
            **values,
            correlation_id=secret_use_correlation_for(**values),
            requested_at=requested_at,
            actor_scopes=command.authority.scopes,
        )

    def authorization_rows(self):
        return tuple(
            self.connection.execute(
                "SELECT authorization_id, workspace_id, secret_reference, "
                "use_intent, actor_subject, correlation_id, requested_at, "
                "operation_id, session_id, run_id, activity_id, effect_id "
                "FROM cpk_secret_use_authorizations "
                "ORDER BY actor_subject, secret_reference, use_intent"
            ).fetchall()
        )

    def complete_reconciliation_snapshot(self):
        return self.complete_snapshot(), self.authorization_rows()

    def lease_observation(self, observed_at, *, expired=False):
        original = PostgresExecutionStore.observe_request_lease_for_update

        def observe(store, request_id):
            return replace(
                original(store, request_id),
                observed_at=observed_at,
                expired=expired,
            )

        return mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            observe,
        )

    @staticmethod
    def observation_request(request):
        return RuntimeEffectObservationRequest(request)


__all__ = [
    "AUTHORITY_ERROR",
    "FailIfFold",
    "FailIfObserver",
    "INVALID_TRUTH_ERROR",
    "NOT_FOUND_ERROR",
    "PostgresEffectAttemptReconciliationFixture",
    "REPLAY_ERROR",
    "RecordingObserver",
    "UnitOfWorkLedger",
]
