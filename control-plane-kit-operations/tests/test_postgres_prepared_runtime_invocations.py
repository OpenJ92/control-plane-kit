"""Prepared runtime targets use a real ordinary start and retained TLS authority."""

from dataclasses import replace
import hashlib
import unittest

import rfc8785

import control_plane_kit_operations as operations
from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_effects import required_secret_uses_for_runtime_effect
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_execution_coordinator as coordinator_fixture
from tests.test_postgres_current_effect_permissions import CurrentEffectPermissionFixture
from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger


class _PreparedRuntimeInvocationFixture(CurrentEffectPermissionFixture):
    def reset_start_truth(self, *, compensation=False, plan=None, desired=None, base=None, product_document=None,
            runtime_authority=None):
        self.assertFalse(compensation)
        self.reference = RuntimeAuthorityReference("prepared-runtime")
        if plan is None:
            plan = ActivityPlan((PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("runtime-a"))),))
        if desired is None:
            desired = DeploymentGraph("desired", runtimes={"runtime-a": RuntimeRecord(
                "runtime-a", RuntimeKind.DOCKER, authority_ref=self.reference)})
        # Reuse the actual graph, approval, request and lifecycle fixture owners.
        # The ledger retains this fixture's isolated owning-suite schema.
        engine = coordinator_fixture.ExecutionCoordinatorTests()
        engine.connection = self.connection
        engine.tracker = UnitOfWorkLedger(self.unit_of_work)
        engine.reset_execution_request(plan=plan,
            base_graph=DeploymentGraph("current") if base is None else base, desired_graph=desired,
            product_document=product_document)
        with self.unit_of_work() as uow:
            self.runtime = uow.stores.runtime_authorities.register(
                workspace_id="workspace-a", authority_ref=self.reference, runtime_kind=RuntimeKind.DOCKER,
                authority=runtime_authority if runtime_authority is not None else RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
                    SecretReference("secret://runtime/ca"), SecretReference("secret://runtime/cert"),
                    SecretReference("secret://runtime/key")),
                admitted_by="operator-a", admitted_at=self.clock())
            uow.commit()
        authority = self.authority()
        opened = engine.lifecycle_with_ids("run-a", "prepared-open", "prepared-claim").execute(
            ClaimAndOpenActivityRun("request-a", authority, ExecutionLeaseDuration(600), IdempotencyKey("prepared-claim")))
        self.runtime_fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("prepared-run-start", "prepared-run-action").execute(
            StartActivityRun("run-a", authority, self.runtime_fence, IdempotencyKey("prepared-run-start")))
        return engine

    def admitted(self, *, scopes=None, factory=None):
        scopes = (self.operate,) if scopes is None else scopes
        bound = self.binding(generation=self.runtime_fence.generation, scopes=scopes)
        start = self.start_command(intent=replace(self.intent(), authority_ref=self.reference),
            fence=self.runtime_fence, authority=self.authority(scopes=scopes))
        started = self.fresh_start(factory).execute(start, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(retained.intent, start.intent)
            self.assertEqual(retained.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
        request = runtime_effect_request_for_intent(start.intent,
            effect_id=started.attempt.original_start_event.event_id, secret_resolution_grants=())
        self.assertEqual(len(required_secret_uses_for_runtime_effect(request, self.runtime)), 3)
        return bound, start, started


class PostgresPreparedRuntimeInvocationTests(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def test_actual_prepared_start_retains_planned_final_with_exact_registration(self):
        bound, _, started = self.admitted()
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=started.attempt.state.identity.activity_id,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "ordinary prepared start lacks its atomic final-dispatch marker")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)
        expected_target = dict(domain="cpk.prepared-runtime-final-target.v1", kind="prepared-runtime-dispatch",
            request_fingerprint=started.attempt.state.request_fingerprint, workspace_id="workspace-a",
            runtime_id="runtime-a", runtime_kind="docker", authority_ref=self.reference.reference_id,
            runtime_registration_id=self.runtime.registration_id,
            required_uses=[["operational", "secret://runtime/ca", "docker.remote-tls.ca-certificate"],
                ["operational", "secret://runtime/cert", "docker.remote-tls.client-certificate"],
                ["operational", "secret://runtime/key", "docker.remote-tls.client-key"]])
        self.assertEqual(marker.target_fingerprint, hashlib.sha256(rfc8785.dumps(expected_target)).hexdigest())

    def test_original_runtime_binding_reads_history_without_new_admission(self):
        bound, start, started = self.admitted()
        ledger = UnitOfWorkLedger(self.unit_of_work)
        service = operations.ExecutionInvocationService(ledger, permission_policy=self.policy(), clock=self.clock)
        bind = getattr(service, "bind_runtime", None)
        self.assertTrue(callable(bind), "invocation service lacks original prepared-runtime binding")
        before = self.attempt_snapshot()
        port = bind(command_context=bound, start=start, started=started)
        self.assertEqual(ledger.active, 0)
        marker = service.inspect_runtime_dispatch(port)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(ledger.active, 0)

    def test_marker_write_and_commit_request_failure_roll_back_the_original_start(self):
        from unittest import mock
        from control_plane_kit_operations.postgres import PostgresExecutionStore
        bound = self.binding(generation=self.runtime_fence.generation)
        start = self.start_command(intent=replace(self.intent(), authority_ref=self.reference), fence=self.runtime_fence)
        before = self.attempt_snapshot()
        original = PostgresExecutionStore.add_command_invocation
        failure = RuntimeError("prepared marker write interrupted")
        def write_then_fail(store, record):
            original(store, record)
            raise failure
        with mock.patch.object(PostgresExecutionStore, "add_command_invocation", write_then_fail):
            with self.assertRaises(RuntimeError) as caught:
                self.fresh_start().execute(start, command_context=bound)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        def refuse_commit():
            uow = self.unit_of_work()
            uow.commit = mock.Mock(side_effect=failure)
            return uow
        with self.assertRaises(RuntimeError) as caught:
            self.fresh_start(refuse_commit).execute(start, command_context=bound)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_historical_binding_preserves_revoked_registration_and_rejects_another_command(self):
        bound, start, started = self.admitted()
        other = self.binding(key="unrelated-command", generation=self.runtime_fence.generation)
        self.revoke()
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
            uow.commit()
        service = operations.ExecutionInvocationService(self.unit_of_work, permission_policy=self.policy(), clock=self.clock)
        port = service.bind_runtime(command_context=bound, start=start, started=started)
        self.assertEqual(service.inspect_runtime_dispatch(port).runtime_registration_id, self.runtime.registration_id)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.bind_runtime(command_context=other, start=start, started=started)

    def test_lost_start_commit_ack_retains_marker_without_returning_a_bindable_start(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from control_plane_kit_operations.effect_attempt_start import ExistingAttempt
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        bound = self.binding(generation=self.runtime_fence.generation)
        start = self.start_command(intent=replace(self.intent(), authority_ref=self.reference), fence=self.runtime_fence)
        failure = RuntimeError("prepared start acknowledgement lost")
        connections = []
        def factory():
            def connect():
                connection = _CommitThenRaiseConnection(self.connect(), failure)
                connections.append(connection)
                return connection
            return PostgresUnitOfWork(connect)
        with self.assertRaises(RuntimeError) as caught:
            self.fresh_start(factory).execute(start, command_context=bound)
        self.assertIs(caught.exception, failure)
        self.assertEqual([connection.commits for connection in connections], [1])
        existing = self.fresh_start().execute(start, command_context=bound)
        self.assertIs(type(existing), ExistingAttempt)
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=existing.attempt.state.identity.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual(marker.original_event_id, existing.attempt.original_start_event.event_id)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        service = operations.ExecutionInvocationService(self.unit_of_work, permission_policy=self.policy(), clock=self.clock)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.bind_runtime(command_context=bound, start=start, started=existing)

    def test_original_lookup_distinguishes_absence_mismatch_and_duplicate_candidates(self):
        bound, _, started = self.admitted()
        identity = started.attempt.state.identity
        args = dict(original_event_id=started.attempt.original_start_event.event_id,
            request_fingerprint=started.attempt.state.request_fingerprint, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.original_final_invocation(identity, **args)
            self.assertEqual(marker.command, bound.receipt)
            with self.assertRaises(operations.ExecutionInvocationConflict):
                uow.stores.execution.original_final_invocation(identity, **dict(args, request_fingerprint="f" * 64))
            with self.assertRaises(operations.ExecutionInvocationConflict):
                uow.stores.execution.original_final_invocation(identity, **dict(args, original_event_id="wrong-event"))
        other = self.binding(key="second-command", generation=self.runtime_fence.generation)
        duplicate = replace(marker, command=other.receipt)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_command_invocation(duplicate)
            uow.commit()
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.original_final_invocation(identity, **args)
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id IN (%s,%s)",
            (marker.invocation_id, duplicate.invocation_id))
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.original_final_invocation(identity, **args))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_corrupt_registration_anchor_refuses_reads_and_current_data_validation(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError
        bound, _, started = self.admitted()
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=started.attempt.state.identity.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        corrupt = replace(marker, runtime_registration_id="missing-registration")
        self.connection.execute("UPDATE cpk_execution_command_invocations SET invocation_id=%s, descriptor=%s WHERE invocation_id=%s",
            (corrupt.invocation_id, Jsonb(corrupt.descriptor()), marker.invocation_id))
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.original_final_invocation(started.attempt.state.identity,
                original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.connection.execute("SELECT descriptor FROM cpk_execution_command_invocations WHERE invocation_id=%s",
            (corrupt.invocation_id,)).fetchone()[0], corrupt.descriptor())

    def test_historical_registration_read_refuses_oversized_retained_json(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.runtime_authorities import RuntimeAuthorityRegistrationError
        bound, _, started = self.admitted()
        original = self.connection.execute("SELECT authority,metadata FROM cpk_runtime_authorities WHERE registration_id=%s",
            (self.runtime.registration_id,)).fetchone()
        for column, oversized in (("authority", dict(padding="x" * 65537)),
                ("metadata", dict(padding="x" * 16385))):
            with self.subTest(column=column):
                self.connection.execute("UPDATE cpk_runtime_authorities SET " + column + "=%s WHERE registration_id=%s",
                    (Jsonb(oversized), self.runtime.registration_id))
                with self.unit_of_work() as uow:
                    with self.assertRaises(RuntimeAuthorityRegistrationError):
                        uow.stores.runtime_authorities.get_registration("workspace-a", self.reference, self.runtime.registration_id)
                    with self.assertRaises(operations.ExecutionInvocationConflict):
                        uow.stores.execution.original_final_invocation(started.attempt.state.identity,
                            original_event_id=started.attempt.original_start_event.event_id,
                            request_fingerprint=started.attempt.state.request_fingerprint,
                            kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
                self.connection.execute("UPDATE cpk_runtime_authorities SET authority=%s,metadata=%s WHERE registration_id=%s",
                    (Jsonb(original[0]), Jsonb(original[1]), self.runtime.registration_id))

    def test_endpoint_drift_under_original_registration_refuses_without_repair(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError
        bound, start, started = self.admitted()
        original = self.connection.execute("SELECT authority FROM cpk_runtime_authorities WHERE registration_id=%s",
            (self.runtime.registration_id,)).fetchone()[0]
        changed = dict(original, endpoint="tcp://drift.example.test:2376")
        self.connection.execute("UPDATE cpk_runtime_authorities SET authority=%s WHERE registration_id=%s",
            (Jsonb(changed), self.runtime.registration_id))
        service = operations.ExecutionInvocationService(self.unit_of_work, permission_policy=self.policy(), clock=self.clock)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.bind_runtime(command_context=bound, start=start, started=started)
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.original_final_invocation(started.attempt.state.identity,
                original_event_id=started.attempt.original_start_event.event_id,
                request_fingerprint=started.attempt.state.request_fingerprint,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.connection.execute("SELECT authority FROM cpk_runtime_authorities WHERE registration_id=%s",
            (self.runtime.registration_id,)).fetchone()[0], changed)


class PostgresPreparedRuntimeAdmissionTests(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from control_plane_kit_core.identity import AuthenticatedPrincipal
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretProviderId
        from control_plane_kit_operations.secret_providers import (
            SecretProviderRegistrationService, RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
            SecretProviderKind, SecretUseAuthorizationService,
        )
        from control_plane_kit_operations.runtime_secret_uses import secret_authorization_command
        from tests.test_managed_application_chain import now
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (PolicyScope.SECRET_PROVIDER_USE,), 1, IdempotencyKey("grant-resolution")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")
        self.bound, self.start, self.started = self.admitted(scopes=self.original.granted_scopes)
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        self.port = self.service.bind_runtime(command_context=self.bound, start=self.start, started=self.started)
        self.request = runtime_effect_request_for_intent(self.start.intent,
            effect_id=self.started.attempt.original_start_event.event_id, secret_resolution_grants=())
        self.uses = required_secret_uses_for_runtime_effect(self.request, self.runtime)
        registrations = SecretProviderRegistrationService(self.unit_of_work)
        provider = registrations.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("runtime"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Runtime target fixture",
            endpoint_reference=SecretProviderEndpointReference("runtime-provider"),
            credential_reference=SecretReference("secret://bootstrap/runtime/token"),
            allowed_reference_prefixes=tuple(use.reference for use in self.uses),
            allowed_intents=tuple(use.intent for use in self.uses), admitted_by="operator-a", admitted_at=now(),
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        for use in self.uses:
            registrations.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a",
                reference=use.reference, provider_registration_id=provider.registration_id,
                allowed_intents=(use.intent,), admitted_by="operator-a", admitted_at=now(),
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        with self.unit_of_work() as uow:
            self.session_id = uow.stores.execution.get_request(self.start.request_id).identity.session_id
        self.authorization_owner = SecretUseAuthorizationService(self.unit_of_work)
        self.authorization_commands = tuple(secret_authorization_command(self.request, use,
            actor_subject=self.original.actor_id, requested_at=now(), actor_scopes=self.original.granted_scopes,
            session_id=self.session_id) for use in self.uses)
        self.authorizations = tuple(self.authorization_owner.authorize(command) for command in self.authorization_commands)
        self.calls = []

    def revoke(self, factory=None):
        return self.permissions(factory).revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.worker, (self.operate,), 2, IdempotencyKey("revoke")))

    def invoke(self, kind, authorization=None):
        permit = self.port.admit(kind, authorization_id=None if authorization is None else authorization.authorization_id)
        self.assertEqual(self.ledger.active, 0)
        with self.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation(self.bound.receipt, permit.record.invocation_id)
        self.assertEqual(retained, permit.record)
        self.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))
        self.calls.append((kind, None if authorization is None else authorization.authorization_id))
        self.port.record_outcome(permit, "returned")
        self.assertEqual(self.ledger.active, 0)
        return permit

    def test_separate_tls_uses_and_final_have_committed_admission_without_new_attempt(self):
        before = self.attempt_snapshot()
        for authorization in self.authorizations:
            self.invoke("secret-resolution", authorization)
        self.invoke("prepared-runtime-dispatch")
        self.assertEqual(len(self.calls), 4)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
        self.assertEqual(len(rows), 4)
        self.assertEqual({row.outcome.value for row in rows}, {"returned"})
        self.assertEqual(self.attempt_snapshot(), before)
        with self.assertRaises(operations.ExecutionInvocationExisting):
            self.port.admit("prepared-runtime-dispatch")
        self.assertEqual(len(self.calls), 4)

    def test_revocation_between_tls_uses_denies_next_without_final_call(self):
        self.invoke("secret-resolution", self.authorizations[0])
        self.revoke()
        with self.assertRaises(operations.ExecutionInvocationDenied):
            self.port.admit("secret-resolution", authorization_id=self.authorizations[1].authorization_id)
        marker = self.service.inspect_runtime_dispatch(self.port)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(len(self.calls), 1)
        with self.unit_of_work() as uow:
            rows = uow.stores.execution.command_invocations(self.bound.receipt).items
        self.assertEqual(sorted(row.disposition.value for row in rows), ["admitted", "denied", "planned"])

    def test_admitted_final_classification_survives_permission_and_runtime_revocation(self):
        permit = self.port.admit("prepared-runtime-dispatch")
        self.assertEqual(self.ledger.active, 0)
        self.revoke()
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
            uow.commit()
        self.port.record_outcome(permit, "returned")
        marker = self.service.inspect_runtime_dispatch(self.port)
        self.assertEqual(marker.outcome.value, "returned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)

    def test_replacement_cannot_substitute_a_new_registration_into_original_target(self):
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", self.reference)
            replacement = uow.stores.runtime_authorities.register(workspace_id="workspace-a",
                authority_ref=self.reference, runtime_kind=RuntimeKind.DOCKER,
                authority=replace(self.runtime.authority, endpoint="tcp://replacement.example.test:2376"),
                admitted_by="operator-a", admitted_at=self.clock())
            uow.commit()
        self.assertNotEqual(replacement.registration_id, self.runtime.registration_id)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            self.port.admit("prepared-runtime-dispatch")
        marker = self.service.inspect_runtime_dispatch(self.port)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(self.calls, [])

    def test_sessionless_and_foreign_session_authorizations_are_not_adopted(self):
        from control_plane_kit_operations.runtime_secret_uses import secret_authorization_command
        before = self.service.inspect_runtime_dispatch(self.port)
        for session in (None, "foreign-session"):
            command = secret_authorization_command(self.request, self.uses[0],
                actor_subject=self.original.actor_id, requested_at=self.clock(),
                actor_scopes=self.original.granted_scopes, session_id=session)
            authorization = self.authorization_owner.authorize(command)
            with self.subTest(session=session), self.assertRaises(operations.ExecutionInvocationConflict):
                self.port.admit("secret-resolution", authorization_id=authorization.authorization_id)
        self.assertEqual(self.service.inspect_runtime_dispatch(self.port), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_invocations(self.bound.receipt).items, (before,))
        self.assertEqual(self.calls, [])

    def test_missing_marker_does_not_create_admission_or_repair(self):
        marker = self.service.inspect_runtime_dispatch(self.port)
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s", (marker.invocation_id,))
        before = self.attempt_snapshot()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            self.port.admit("prepared-runtime-dispatch")
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    def test_lost_final_admission_ack_returns_no_permit_or_dispatch(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        from tests.test_managed_application_chain import now
        armed, connections = [], []
        def connect():
            connection = self.connect()
            if armed:
                connection = _CommitThenRaiseConnection(connection, RuntimeError("runtime commit ack lost"))
                connections.append(connection)
            return connection
        service = operations.ExecutionInvocationService(lambda: PostgresUnitOfWork(connect),
            permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=self.bound, start=self.start, started=self.started)
        armed.append(True)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("prepared-runtime-dispatch")
        marker = self.service.inspect_runtime_dispatch(self.port)
        self.assertEqual((marker.disposition.value, marker.outcome.value), ("admitted", "pending"))
        self.assertEqual([connection.commits for connection in connections], [1])
        self.assertEqual(self.calls, [])
        with self.assertRaises(operations.ExecutionInvocationExisting):
            self.port.admit("prepared-runtime-dispatch")

    def test_current_graph_drift_refuses_before_a_new_capability(self):
        before = self.service.inspect_runtime_dispatch(self.port)
        self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 WHERE workspace_id='workspace-a'")
        with self.assertRaises(operations.ExecutionInvocationConflict):
            self.port.admit("prepared-runtime-dispatch")
        self.assertEqual(self.service.inspect_runtime_dispatch(self.port), before)
        self.assertEqual(self.calls, [])

    def test_paused_run_refuses_new_secret_and_final_admission(self):
        from control_plane_kit_operations.lifecycle import PauseActivityRun, RunLifecycleCommandService
        from tests.test_managed_application_chain import now
        from uuid import uuid4
        paused = RunLifecycleCommandService(self.unit_of_work, clock=now, id_factory=lambda: uuid4().hex).execute(
            PauseActivityRun("run-a", self.start.authority, self.start.fence, IdempotencyKey("runtime-pause")))
        self.assertEqual(paused.run.status.value, "paused")
        before = self.service.inspect_runtime_dispatch(self.port)
        for kind, authorization in (("secret-resolution", self.authorizations[0].authorization_id),
                ("prepared-runtime-dispatch", None)):
            with self.subTest(kind=kind), self.assertRaises(operations.ExecutionInvocationConflict):
                self.port.admit(kind, authorization_id=authorization)
        self.assertEqual(self.service.inspect_runtime_dispatch(self.port), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_invocations(self.bound.receipt).items, (before,))
        self.assertEqual(self.calls, [])

    def admit_final_with_factory(self, factory):
        from tests.test_managed_application_chain import now
        service = operations.ExecutionInvocationService(factory, permission_policy=self.policy(), clock=now)
        port = service.bind_runtime(command_context=self.bound, start=self.start, started=self.started)
        permit = port.admit("prepared-runtime-dispatch")
        self.calls.append(("prepared-runtime-dispatch", None))
        port.record_outcome(permit, "returned")
        return service.inspect_runtime_dispatch(port)

    def test_revoke_wins_the_real_final_admission_lock(self):
        first, second = self.opposing_commands(lambda factory: self.revoke(factory),
            self.admit_final_with_factory, self.permission_lock, connection_query=self.permission_lock)
        self.assertEqual(first.result().receipt.revision, 3)
        with self.assertRaises(operations.ExecutionInvocationDenied):
            second.result()
        self.assertEqual(self.service.inspect_runtime_dispatch(self.port).disposition.value, "denied")
        self.assertEqual(self.calls, [])

    def test_final_admission_wins_revoke_and_retains_the_original_result(self):
        first, second = self.opposing_commands(self.admit_final_with_factory,
            lambda factory: self.revoke(factory), self.permission_lock, connection_query=self.permission_lock)
        self.assertEqual(first.result().outcome.value, "returned")
        self.assertEqual(second.result().receipt.revision, 3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.inspect_runtime_dispatch(self.port).outcome.value, "returned")
