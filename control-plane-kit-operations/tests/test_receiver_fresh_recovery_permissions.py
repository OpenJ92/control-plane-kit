"""#1904 original recovery gains fresh permission only after lifecycle serialization."""

import unittest

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, NewlyStarted
from control_plane_kit_operations.execution_lease_recovery_interpreter import ExecutionLeaseRecoveryCommandService
from control_plane_kit_operations.failed_run_compensation import FailedRunCompensationCommandService
from control_plane_kit_operations.failed_run_compensation_attempt import (
    FailedRunCompensationAttemptError, FailedRunCompensationAttemptStartService,
)
from control_plane_kit_operations.lifecycle import RunLifecycleError
from tests.activity_run_retry_interpreter_fixture import PostgresActivityRunRetryFixture
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests.failed_run_compensation_attempt_fixture import FailedRunCompensationAttemptFixture
from tests.failed_run_compensation_fixture import FailedRunCompensationFixture
from tests.lifecycle_lock_fixture import (
    LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK, SESSION_LOCK, WORKSPACE_LOCK,
)
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.postgres_effect_attempt_start_fixture import PostgresEffectAttemptStartFixture
from tests.receiver_fresh_permission_witness import FreshPermissionWitness


class FreshRecoveryLockWitness(FreshPermissionWitness):
    def assert_fresh_waits_before_execution_rows(self, execute):
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
            self.assert_row_lockable(REQUEST_LOCK, ("request-a",))
            self.assert_row_lockable(RUN_LOCK, ("run-a",))
            self.assert_row_lockable(SESSION_LOCK, ("session-a",))
            self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
        return future.result(timeout=1)


class ReceiverFreshLeasePermissionTests(PostgresExecutionLeaseRecoveryFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_renewals_and_takeover_recheck_changed_pins_after_lifecycle_wait(self):
        for decision in (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                         RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                         RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM):
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                result = self.assert_rechecks_pins_while_waiting(lambda factory:
                    ExecutionLeaseRecoveryCommandService(factory,
                        id_factory=GeneratedIds("lease-recheck")).execute(command), RunLifecycleError)
                self.assertFalse(result.replayed)

    def test_active_expired_renewal_and_takeover_wait_for_lifecycle_before_rows(self):
        for decision in (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                         RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                         RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM):
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                result = self.assert_fresh_waits_before_execution_rows(lambda factory:
                    ExecutionLeaseRecoveryCommandService(factory, id_factory=GeneratedIds("lease-lock")).execute(command))
                self.assertFalse(result.replayed)


class ReceiverFreshRetryPermissionTests(PostgresActivityRunRetryFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_original_failed_retry_waits_for_lifecycle_before_prior_and_latest_rows(self):
        self.reset_retry_truth()
        command = self.retry_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            ActivityRunRetryCommandService(factory, id_factory=GeneratedIds("retry-lock")).execute(command))
        self.assertFalse(result.replayed)
        self.assertEqual(result.run.retry.prior_run_id, "run-a")


class ReceiverFreshCompensationPermissionTests(FailedRunCompensationFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_begin_compensation_rechecks_changed_pins_after_lifecycle_wait(self):
        self.seed_truth()
        command = self.command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            FailedRunCompensationCommandService(factory, clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=GeneratedIds("compensation-recheck")).execute(command), RunLifecycleError)
        self.assertFalse(result.replayed)

    def test_begin_compensation_waits_for_lifecycle_before_execution_and_program_truth(self):
        self.seed_truth()
        command = self.command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            FailedRunCompensationCommandService(factory, clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=GeneratedIds("compensation-lock")).execute(command))
        self.assertFalse(result.replayed)


class ReceiverFreshInversePermissionTests(FailedRunCompensationAttemptFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_base_inverse_refuses_inherited_receiver_product_at_same_legacy_coordinate(self):
        from dataclasses import replace
        from control_plane_kit_core.planning import NodeTarget, ReconcileNode
        from control_plane_kit_core.products import (
            ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
            ProductIdentity, ProductRuntimeContract, ProviderRuntimePort,
        )
        from control_plane_kit_core.runtime_effects import RuntimeProductMaterial
        from control_plane_kit_operations.planning import DesiredGraphCommandService, SetDesiredGraph
        from control_plane_kit_operations.products import InlineDescriptorSource
        from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation
        from control_plane_kit_operations.workflows import IdempotencyKey
        from tests.failed_run_compensation_fixture import Sequence
        from tests.receiver_storage_fixture import ReceiverStorageFixture
        material = {}

        def introduce_desired(case):
            graph, _, _ = ReceiverStorageFixture.receiver_graph(case, node_id="node-a",
                target_changes={"runtime_id": "runtime-a"})
            node = replace(graph.nodes["node-a"], runtime_id="runtime-a")
            graph = replace(graph, nodes={"node-a": node}, runtimes={"runtime-a":
                replace(graph.runtimes["docker"], runtime_id="runtime-a")})
            product = ContainerServerProduct(ProductIdentity("test", "inverse-receiver", 1),
                OciImageReference("ghcr.io", "test/inverse-receiver", "sha256:" + "b" * 64),
                ProductRuntimeContract(sockets=node.sockets, provider_ports=(ProviderRuntimePort("http", 8000),),
                    capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
                    configuration_artifacts=node.configuration_artifacts, public_environment=node.public_environment))
            with case.unit_of_work() as uow:
                registered = uow.stores.registered_products.register(workspace_id="workspace-a",
                    descriptor_document=ProductDescriptorCodec().encode_document(product),
                    source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-08-25T11:50:00Z")
                workspace = uow.stores.workspaces.get("workspace-a")
                uow.commit()
            node = replace(node, metadata={"product_identity": registered.reference.identity.key,
                "product_descriptor_digest": registered.reference.descriptor_sha256.value})
            graph = replace(graph, nodes={"node-a": node})
            pins = ReceiverLifecycleExpectation(workspace.current_graph_id, workspace.current_realized_projection_id,
                workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision)
            DesiredGraphCommandService(case.unit_of_work, clock=lambda: "2026-08-25T11:50:30Z",
                id_factory=Sequence("graph-desired", "receiver-desired-action")).execute(SetDesiredGraph(
                    "session-a", "workspace-a", "operator-a", graph, None, IdempotencyKey("receiver-desired"),
                    receiver_lifecycle=pins))
            material["start-node"] = {"products": (RuntimeProductMaterial("node-a", "runtime-a",
                registered.reference, product, public_environment=node.public_environment),)}

        # Forward completion is an explicit retained premise. C2 introduction,
        # request admission, compensation admission and inverse refusal are real.
        self.seed_truth(node_operation=ReconcileNode(NodeTarget("node-a")),
            desired_owner=introduce_desired, success_material=material)
        self.service(Sequence("program-a", "compensation-started", "action-a")).execute(self.command())
        self.connection.execute("UPDATE cpk_execution_requests SET claimed_at='2098-01-01T00:00:00Z', "
            "lease_expires_at='2099-01-01T00:00:00Z' WHERE request_id='request-a'")
        with self.unit_of_work() as uow:
            _, program = uow.stores.failed_run_compensations.get("program-a")
            step = program.steps[0]
            self.assertEqual(step.source_effect.attempt_identity.activity_id, "start-node")
            source = uow.stores.effect_attempt_intents.get(step.source_effect.attempt_identity)
            inherited = replace(source.intent, operation=step.operation)
            workspace = uow.stores.workspaces.get("workspace-a")
            self.assertEqual(uow.stores.graphs.receiver_bindings("workspace-a", workspace.current_graph_id,
                workspace.current_realized_projection_id), ())
            self.assertEqual(len(uow.stores.graphs.receiver_bindings("workspace-a", workspace.desired_graph_id,
                workspace.desired_realized_projection_id)), 1)
        before = self.permission_truth(), self.binding_snapshot(), self.source_truth_snapshot()
        sequence = Sequence("forbidden-inverse")
        with self.assertRaises(FailedRunCompensationAttemptError):
            FailedRunCompensationAttemptStartService(self.unit_of_work, id_factory=sequence).execute(
                self.start_command(intent=inherited))
        self.assertEqual(sequence.calls, [])
        self.assertEqual((self.permission_truth(), self.binding_snapshot(), self.source_truth_snapshot()), before)

    def test_inverse_uses_original_base_runtime_authority_without_retranslation(self):
        from dataclasses import replace
        from control_plane_kit_core.operations import EffectAttemptStatus
        from control_plane_kit_core.planning import ReconcileRuntime, RuntimeTarget
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        from tests.graph_lineage_fixture import execution_graph
        from tests.failed_run_compensation_fixture import Sequence
        graphs = {}
        authority = RuntimeAuthorityReference("desired-authority")
        for key, reference in (("graph-current", RuntimeAuthorityReference("base-authority")),
                               ("graph-desired", authority)):
            graph = execution_graph(key, node_ids=("node-a",))
            graphs[key] = replace(graph, runtimes={"runtime-a": replace(graph.runtimes["runtime-a"], authority_ref=reference)})
        self.seed_truth(graphs=graphs, runtime_operation=ReconcileRuntime(RuntimeTarget("runtime-a")),
            success_material={key: {"authority_ref": authority} for key in ("start-runtime", "start-node")},
            actor_scopes=tuple(PolicyScope))
        self.service(Sequence("program-a", "compensation-started", "action-a")).execute(self.command())
        self.connection.execute("UPDATE cpk_execution_requests SET claimed_at='2098-01-01T00:00:00Z', "
            "lease_expires_at='2099-01-01T00:00:00Z' WHERE request_id='request-a'")
        with self.unit_of_work() as uow:
            _, program = uow.stores.failed_run_compensations.get("program-a")
            intents = tuple(replace(uow.stores.effect_attempt_intents.get(step.source_effect.attempt_identity).intent,
                operation=step.operation) for step in program.steps)
        self.assertEqual(program.steps[0].source_effect.attempt_identity.activity_id, "start-node")
        self.assertEqual(program.steps[1].source_effect.attempt_identity.activity_id, "start-runtime")
        # The desired-side StopNode inverse remains lawful with inherited B.
        self.attempt_service("inverse-start-a").execute(self.start_command(intent=intents[0]))
        self.fold_bound_attempt(EffectAttemptStatus.SUCCEEDED)
        before = self.permission_truth(), self.binding_snapshot(), self.source_truth_snapshot()
        sequence = Sequence("forbidden-inverse")
        with self.assertRaises(FailedRunCompensationAttemptError):
            FailedRunCompensationAttemptStartService(self.unit_of_work, id_factory=sequence).execute(
                self.start_command(position=2, intent=intents[1]))
        self.assertEqual(sequence.calls, [])
        self.assertEqual((self.permission_truth(), self.binding_snapshot(), self.source_truth_snapshot()), before)

    def test_inverse_rechecks_required_program_set_after_waiting_on_workspace(self):
        self.seed_admitted_program()
        command = self.start_command()
        from psycopg.types.json import Jsonb
        row = self.connection.execute("SELECT to_jsonb(t) FROM cpk_failed_run_compensation_steps t "
            "WHERE program_id='program-a' ORDER BY position DESC LIMIT 1").fetchone()[0]
        deleted = False
        try:
            with self.blocked_command(WORKSPACE_LOCK, ("workspace-a",), lambda factory:
                    FailedRunCompensationAttemptStartService(factory,
                        id_factory=GeneratedIds("inverse-set-recheck")).execute(command)) as future:
                # The real owner has selected and locked its earlier attempt
                # set. Corrupt only the retained program collection while it
                # waits; it must reread/refuse rather than use that old set.
                self.assertEqual(self.connection.execute("DELETE FROM cpk_failed_run_compensation_steps "
                    "WHERE program_id='program-a' AND position=%s", (row["position"],)).rowcount, 1)
                deleted = True
                before = self.permission_truth()
            with self.assertRaises(FailedRunCompensationAttemptError):
                future.result(timeout=1)
            self.assertEqual(self.permission_truth(), before)
        finally:
            if deleted:
                self.connection.execute("INSERT INTO cpk_failed_run_compensation_steps SELECT * FROM "
                    "jsonb_populate_record(NULL::cpk_failed_run_compensation_steps,%s)", (Jsonb(row),))
        self.assertFalse(self.attempt_service("inverse-set-valid").execute(command).replayed)

    def test_fresh_inverse_rechecks_changed_pins_after_lifecycle_wait(self):
        self.seed_admitted_program()
        command = self.start_command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            FailedRunCompensationAttemptStartService(factory,
                id_factory=GeneratedIds("inverse-recheck")).execute(command), FailedRunCompensationAttemptError)
        self.assertFalse(result.replayed)

    def test_fresh_inverse_waits_for_lifecycle_before_request_run_and_attempts(self):
        self.seed_admitted_program()
        command = self.start_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            FailedRunCompensationAttemptStartService(factory, id_factory=GeneratedIds("inverse-lock")).execute(command))
        self.assertFalse(result.replayed)


class ReceiverFreshEffectPermissionTests(PostgresEffectAttemptStartFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_fresh_effect_rechecks_changed_pins_after_lifecycle_wait(self):
        command = self.start_command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            EffectAttemptStartService(factory, id_factory=GeneratedIds("effect-recheck")).execute(command),
            EffectAttemptStartError)
        self.assertIsInstance(result, NewlyStarted)

    def test_fresh_effect_waits_for_lifecycle_before_request_run_and_attempts(self):
        command = self.start_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            EffectAttemptStartService(factory, id_factory=GeneratedIds("effect-lock")).execute(command))
        self.assertIsInstance(result, NewlyStarted)
