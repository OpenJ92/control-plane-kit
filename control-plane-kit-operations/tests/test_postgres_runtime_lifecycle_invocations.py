"""Remote lifecycle targets reuse the actual prepared-start owners."""

import asyncio
from dataclasses import replace
import hashlib
import unittest

import rfc8785

import control_plane_kit_operations as operations
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, NodeTarget, PlannedActivity, ReconcileRuntime,
    RemoveNodeResource, RemoveRuntimeResource, RuntimeTarget, StartRuntime, StopNode, StopRuntime,
)
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_reconciliation import ReconcileEffectAttempt
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.prepared_runtime_invocations import (
    prepared_runtime_invocation_profile, prepared_runtime_observation_supported,
    prepared_runtime_reconciliation_target_fingerprint, runtime_preparation_candidate,
)
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture, product_material
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture
from tests.test_postgres_runtime_reconciliation_invocations import (
    _ControlledReconciliationObserver, _PreparedRuntimeReconciliationFixture,
)
from tests.test_runtime_interpreter_dispatcher import _registered_product, graph_with_node


class PostgresRuntimeLifecycleInvocationTargets(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def reset_start_truth(self, *, compensation=False, plan=None, desired=None):
        plan = ActivityPlan((PlannedActivity(ActivityId("reconcile-runtime"),
            ReconcileRuntime(RuntimeTarget("runtime-a"))),))
        base = DeploymentGraph("current", runtimes={"runtime-a": RuntimeRecord(
            "runtime-a", RuntimeKind.DOCKER, authority_ref=RuntimeAuthorityReference("prepared-runtime"))})
        super().reset_start_truth(compensation=compensation, plan=plan, desired=desired, base=base)

    def test_remote_reconcile_start_retains_its_atomic_prepared_marker(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        intent = replace(self.intent(activity_id="reconcile-runtime",
            operation=ReconcileRuntime(RuntimeTarget("runtime-a"))), authority_ref=self.reference)
        transition = self.transition(identity=self.identity(activity_id="reconcile-runtime"), intent=intent)
        command = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        started = self.fresh_start().execute(command, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=intent.activity_id.value, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "remote ReconcileRuntime lacks its atomic prepared dispatch marker")
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)


class _RuntimeLifecycleFixture(_PreparedRuntimeReconciliationFixture):
    operation = ReconcileRuntime(RuntimeTarget("runtime-a"))
    activity_id = "runtime-lifecycle"

    def reset_start_truth(self, **_options):
        reference = RuntimeAuthorityReference("prepared-runtime")
        plan = ActivityPlan((PlannedActivity(ActivityId(self.activity_id), self.operation),))
        document = None
        if type(self.operation.target) is NodeTarget:
            product = _registered_product()
            material = product_material()
            document = ProductDescriptorCodec().encode_document(material.product)
            product = RegisteredProduct.from_document(workspace_id="workspace-a",
                descriptor_document=document, source=product.source,
                imported_by="operator-a", imported_at=self.clock())
            base = graph_with_node(RuntimeKind.DOCKER, authority_ref=reference, registered_product=product)
            base = replace(base, nodes={"api": replace(base.nodes["api"],
                secret_selections=material.secret_selections,
                sockets=material.product.runtime_contract.sockets,
                block_spec=replace(base.nodes["api"].block_spec,
                    verification=material.product.runtime_contract.verification))})
        else:
            base = DeploymentGraph("current", runtimes={"runtime-a": RuntimeRecord(
                "runtime-a", RuntimeKind.DOCKER, authority_ref=reference)})
        super().reset_start_truth(plan=plan, base=base, desired=replace(base, name="desired"),
            product_document=document)

    def start_material(self):
        owner, command = self.composition()
        context = owner._load_context(command.execution)
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        start = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence,
            authority=self.authority(scopes=self.original.granted_scopes))
        return start

    def seed_start(self):
        bound = self.binding(generation=self.runtime_fence.generation, scopes=self.original.granted_scopes)
        start = self.start_material()
        started = self.fresh_start().execute(start, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        return bound, start, started

    def assert_execution_carriage(self):
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("failed", 1))
        self.assertEqual(len(self.adapter.requests), 1)
        self.assertEqual(self.adapter.requests[0].operation, self.operation)
        self.assertEqual(len(self.adapter.requests[0].secret_resolution_grants), 3)
        self.assertEqual(len(self.adapter.committed), 4)
        self.assertEqual(self.adapter.active_transactions, [0] * 4)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(self.identity(activity_id=self.activity_id))
            marker = uow.stores.execution.original_final_invocation(attempt.state.identity,
                original_event_id=attempt.original_start_event.event_id,
                request_fingerprint=attempt.state.request_fingerprint,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual((marker.disposition.value, marker.outcome.value), ("admitted", "returned"))
        self.assertEqual(attempt.state.status.value, "failed")
        self.assertEqual(self.observer.calls, [])
        return command, attempt

    def assert_fresh_observation(self):
        _, _, started, original, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        result = asyncio.run(coordinator.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("uncertain", 1))
        self.assertEqual(len(observer.requests), 1)
        self.assertEqual(observer.requests[0][0].runtime_request.operation, self.operation)
        self.assertEqual(len(observer.committed), 4)
        current = self.observation_row(service, observer)
        self.assertEqual((current.disposition.value, current.outcome.value), ("admitted", "returned"))
        self.assertNotEqual(current.command, original.command)
        self.assertEqual(current.original_event_id, started.attempt.original_start_event.event_id)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_invocation(original.command, original.invocation_id), original)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1)
        self.assertEqual(self.adapter.requests, [])


class PostgresRuntimeLifecycleInvocationTests(_RuntimeLifecycleFixture, unittest.TestCase):
    def test_reconcile_runtime_executes_with_committed_tls_and_final_admission(self):
        self.assert_execution_carriage()

    def test_reconcile_runtime_fresh_observation_uses_original_history(self):
        self.assert_fresh_observation()


class PostgresNodeTeardownInvocationTests(_RuntimeLifecycleFixture, unittest.TestCase):
    operation = StopNode(NodeTarget("api"))

    def test_node_teardown_executes_with_committed_tls_and_final_admission(self):
        self.assert_execution_carriage()
        material, = self.adapter.requests[0].products
        self.assertEqual(len(material.secret_selections), 1)
        self.assertEqual(len(material.product.runtime_contract.verification.checks), 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 3)

    def test_node_teardown_fresh_observation_uses_original_history(self):
        self.assert_fresh_observation()

    def test_invalid_original_node_material_cannot_commit_a_start(self):
        bound = self.binding(generation=self.runtime_fence.generation, scopes=self.original.granted_scopes)
        start = self.start_material()
        material, = start.intent.products
        before = self.attempt_snapshot()
        # Missing material reaches the new profile; duplicates and mismatched
        # nodes retain their existing earlier command/receiver defenses.
        for products, error in (((), ValueError), ((material, material), InvalidOperationCommand),
                ((replace(material, node_id="another-node"),), EffectAttemptStartConflict)):
            with self.subTest(products=products), self.assertRaises(error):
                intent = replace(start.intent, products=products)
                command = replace(start, intent=intent,
                    transition=self.transition(identity=start.transition.identity, intent=intent))
                self.fresh_start().execute(command, command_context=bound)
            self.assertEqual(self.attempt_snapshot(), before)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)

    def test_original_node_runtime_target_is_independent_and_storage_rejects_drift(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.postgres.current_data_validation import CurrentRowDrift
        from control_plane_kit_operations.postgres.execution import validate_execution_invocation_rows
        bound, start, started = self.seed_start()
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        target = dict(domain="cpk.prepared-runtime-final-target.v1", kind="prepared-runtime-dispatch",
            request_fingerprint=started.attempt.state.request_fingerprint, workspace_id="workspace-a",
            runtime_id="runtime-a", runtime_kind="docker", authority_ref=self.reference.reference_id,
            runtime_registration_id=self.runtime.registration_id,
            required_uses=[["operational", "secret://runtime/ca", "docker.remote-tls.ca-certificate"],
                ["operational", "secret://runtime/cert", "docker.remote-tls.client-certificate"],
                ["operational", "secret://runtime/key", "docker.remote-tls.client-key"]])
        self.assertEqual(marker.target_fingerprint, hashlib.sha256(rfc8785.dumps(target)).hexdigest())
        target["runtime_id"] = "different-runtime"
        corrupt = replace(marker, target_fingerprint=hashlib.sha256(rfc8785.dumps(target)).hexdigest())
        self.connection.execute("UPDATE cpk_execution_command_invocations SET invocation_id=%s, "
            "target_fingerprint=%s, descriptor=%s WHERE invocation_id=%s",
            (corrupt.invocation_id, corrupt.target_fingerprint, Jsonb(corrupt.descriptor()), marker.invocation_id))
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.command_invocation(bound.receipt, corrupt.invocation_id)
        with self.assertRaises(CurrentRowDrift):
            validate_execution_invocation_rows(self.connection)
        self.assertEqual(self.adapter.requests, [])


class PostgresStopRuntimeObservationTests(_RuntimeLifecycleFixture, unittest.TestCase):
    operation = StopRuntime(RuntimeTarget("runtime-a"))

    def test_fresh_unsupported_and_command_replay_do_not_authorize_observe_or_fold(self):
        *_, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        result = asyncio.run(coordinator.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 1))
        self.assertEqual(asyncio.run(coordinator.execute_managed(command)), result)
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)
        self.assert_no_observed_fold()

    def test_direct_owner_and_binder_refuse_unsupported_composition(self):
        _, _, started, _, service = self.unresolved_original()
        observer = _ControlledReconciliationObserver(self)
        owner, command, context = self.direct(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=context)
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            service.bind_reconciliation(command_context=context, command=command, attempt=started.attempt,
                intent_record=intent, runtime_authority=self.runtime)
        self.assertEqual(observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assert_no_observed_fold()

    def test_planned_history_precedes_unsupported(self):
        self.seed_start()
        service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=self.clock)
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationPlanned):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_missing_original_precedes_unsupported(self):
        *_, marker, service = self.unresolved_original()
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s", (marker.invocation_id,))
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_denied_original_precedes_unsupported_after_permission_is_restored(self):
        bound, start, started = self.seed_start()
        service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=self.clock)
        port = service.bind_runtime(command_context=bound, start=start, started=started)
        self.revoke_operate()
        with self.assertRaises(operations.ExecutionInvocationDenied):
            port.admit("prepared-runtime-dispatch")
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (self.operate,), 3, IdempotencyKey("restore-runtime-operation")))
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationDenied):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_corrupt_original_target_precedes_unsupported(self):
        from psycopg.types.json import Jsonb
        *_, marker, service = self.unresolved_original()
        corrupt = replace(marker, target_fingerprint="f" * 64)
        self.connection.execute("UPDATE cpk_execution_command_invocations SET invocation_id=%s, "
            "target_fingerprint=%s, descriptor=%s WHERE invocation_id=%s",
            (corrupt.invocation_id, corrupt.target_fingerprint, Jsonb(corrupt.descriptor()), marker.invocation_id))
        observer = _ControlledReconciliationObserver(self)
        coordinator, command = self.composition_for_reconciliation(observer, service)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(coordinator.execute_managed(command))
        self.assertEqual(observer.requests, [])
        self.assert_no_observed_fold()

    def test_terminal_fold_replay_precedes_fresh_unsupported_rule(self):
        command, attempt = self.assert_execution_carriage()
        self.revoke_operate()
        owner = EffectAttemptReconciliationService(self.ledger, object(), object(), permission_policy=self.policy())
        result = owner.execute(ReconcileEffectAttempt("request-a", attempt.state.identity,
            command.execution.authority, command.execution.fence))
        self.assertIs(type(result), operations.ExistingFold)
        self.assertEqual(result.attempt, attempt)

    def test_storage_independently_refuses_unsupported_observation(self):
        from control_plane_kit_operations.postgres.current_data_validation import CurrentRowDrift
        from control_plane_kit_operations.postgres.execution import (
            _INVOCATION_COLUMNS, _invocation_values, validate_execution_invocation_rows,
        )
        _, _, started, marker, _ = self.unresolved_original()
        context = self.binding(key="unsupported-observation", generation=self.runtime_fence.generation,
            scopes=self.original.granted_scopes)
        target = prepared_runtime_reconciliation_target_fingerprint(original_invocation_id=marker.invocation_id,
            original_target_fingerprint=marker.target_fingerprint, original_request_fingerprint=marker.request_fingerprint,
            runtime_registration_id=marker.runtime_registration_id)
        row = operations.ExecutionInvocationRecord(context.receipt, marker.workspace_id, marker.activity_id,
            ExecutionInvocationKind.RECONCILIATION_OBSERVATION, target, marker.decision_at,
            attempt=started.attempt.state.identity.attempt, original_event_id=marker.original_event_id,
            request_fingerprint=marker.request_fingerprint, decision_at=marker.decision_at,
            decision=marker.decision, disposition=operations.ExecutionInvocationDisposition.ADMITTED,
            outcome=operations.ExecutionInvocationOutcome.PENDING)
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.add_command_invocation(row)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)
        # Inject impossible storage truth to test reads and data audit independently
        # of coordinator/binder refusal; this row is never an admission permit.
        self.connection.execute("INSERT INTO cpk_execution_command_invocations ("
            + ", ".join(_INVOCATION_COLUMNS) + ") VALUES ("
            + ", ".join("%s" for _ in _INVOCATION_COLUMNS) + ")", _invocation_values(row))
        with self.unit_of_work() as uow, self.assertRaises(operations.ExecutionInvocationConflict):
            uow.stores.execution.command_invocation(context.receipt, row.invocation_id)
        with self.assertRaises(CurrentRowDrift):
            validate_execution_invocation_rows(self.connection)
        self.assert_no_observed_fold()


class PreparedRuntimeLifecycleProfileTests(EffectAttemptIntentFixture, unittest.TestCase):
    def profile(self, intent):
        runtime = RegisteredRuntimeAuthority.from_authority(workspace_id=intent.source.workspace_id,
            authority_ref=intent.authority_ref, runtime_kind=RuntimeKind.DOCKER,
            authority=RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
                SecretReference("secret://runtime/ca"), SecretReference("secret://runtime/cert"),
                SecretReference("secret://runtime/key")), admitted_by="operator-a", admitted_at="2026-10-25T20:00:00Z")
        return prepared_runtime_invocation_profile(intent, original_event_id="original-event", runtime_authority=runtime)

    def test_closed_execution_and_observation_sets(self):
        for operation in (StartRuntime(RuntimeTarget("runtime-a")), ReconcileRuntime(RuntimeTarget("runtime-a")),
                StopRuntime(RuntimeTarget("runtime-a")), RemoveRuntimeResource(RuntimeTarget("runtime-a")),
                StopNode(NodeTarget("api")), RemoveNodeResource(NodeTarget("api"))):
            products = (product_material(),) if type(operation.target) is NodeTarget else ()
            intent = self.intent(operation=operation, products=products, process_delivery=False)
            with self.subTest(operation=operation):
                self.assertTrue(runtime_preparation_candidate(intent))
                self.assertEqual(prepared_runtime_observation_supported(intent), type(operation) is not StopRuntime)
                self.assertEqual(len(self.profile(intent).required_uses), 3)
        self.assertFalse(runtime_preparation_candidate(self.intent(process_delivery=False)))
        self.assertFalse(runtime_preparation_candidate(replace(intent, authority_ref=None)))

    def test_cleanup_ignores_historical_workload_pull_and_probe_roles_but_start_recipe_is_preserved(self):
        material = product_material()
        cleanup = self.intent(operation=RemoveNodeResource(NodeTarget("api")), products=(material,), process_delivery=False)
        profile = self.profile(cleanup)
        self.assertEqual({use.intent.value for use in profile.required_uses}, {
            "docker.remote-tls.ca-certificate", "docker.remote-tls.client-certificate", "docker.remote-tls.client-key"})
        historical_start = replace(cleanup, operation=StartRuntime(RuntimeTarget("docker")))
        self.assertEqual(len(self.profile(historical_start).required_uses), 5)
        for operation in (ReconcileRuntime(RuntimeTarget("docker")), StopRuntime(RuntimeTarget("docker")),
                RemoveRuntimeResource(RuntimeTarget("docker"))):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.profile(replace(cleanup, operation=operation))
