"""#1923 ordinary new-node preparation through the durable start boundary.

Real creation and an approved, simulated runtime start establish accepted
zero-slot current truth. Recorded run/lease overlays only provide the tested
configuration start's existing worker clock and fence. No provider is called.
"""
from dataclasses import replace
from hashlib import sha256
from itertools import count

import rfc8785

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef, ConfigurationInstanceSelection,
)
from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, NodeTarget, PlannedActivity, ReconcileNode, RiskLevel, StartNode,
    RuntimeTarget, StartRuntime,
)
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectResult
from control_plane_kit_operations.advancement import AdvanceCurrentGraph, CurrentGraphAdvancementCommandService
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ActivityRunRecord, AdmittedRun,
    ApprovalDecisionKind, ApprovalDecisionRecord, ApprovalRequestRecord, BoundedEvidence,
    GraphVersionRecord, OperationSessionRecord, OperationSessionStatus, RetryIdentity,
)
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests import test_execution_coordinator as coordinator_fixture
from tests.graph_lineage_fixture import execution_graph, seed_identity_graphs
from tests.postgres_effect_attempt_intent_store_fixture import PostgresEffectAttemptIntentStoreFixture
from tests.receiver_scope_history_fixture import admit_fixture_plan
from tests.test_runtime_effect_translation import _configuration_graph, _configuration_product, _context


class ConfigurationPreparationFixture(PostgresEffectAttemptIntentStoreFixture):
    def create_runtime_origin(self, base):
        """Compose real owners; retain all namespaced setup history on reset."""
        WorkspaceCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T12:00:00Z", id_factory=lambda: "graph-initial").create(
                CreateWorkspace("workspace-a", "Configuration preparation", "operator-a", IdempotencyKey("origin-create")))
        plan = ActivityPlan((PlannedActivity(ActivityId("origin-runtime"), StartRuntime(RuntimeTarget("docker"))),))
        requirement = ApprovalPolicy().requirement_for(plan)
        with self.unit_of_work() as uow:
            stores = uow.stores
            stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-current", workspace_id="workspace-a",
                version=2, graph=base, created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            stores.activity_history.add_session(OperationSessionRecord("origin-session", "workspace-a", "operator-a",
                "Establish runtime", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("origin-plan", "origin-session", "graph-initial", "graph-current",
                ActivityPlanStatus.PLANNED, "2026-07-22T12:02:00Z", plan,
                base_realized_projection_id=workspace.current_realized_projection_id,
                desired_realized_projection_id=workspace.desired_realized_projection_id,
                desired_graph_revision=workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord("origin-approval", "origin-session",
                ActivityPlanApprovalSubject("origin-plan"), "operator-a", "2026-07-22T12:03:00Z",
                requirement.required_scope, requirement.max_risk, requirement.destructive))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord("origin-decision", "origin-approval",
                "manager-a", ApprovalDecisionKind.APPROVED, requirement.required_scope, "2026-07-22T12:03:30Z"))
            uow.commit()
        admit_fixture_plan(self, request_id="origin-request", session_id="origin-session", plan_id="origin-plan",
            approval_request_id="origin-approval", key="origin-admit")
        engine = coordinator_fixture.ExecutionCoordinatorTests()
        engine.database_url, engine.connection = self.database_url, self.connection
        identities = count(1)
        engine.ids = lambda: "origin-generated-" + str(next(identities))
        engine.tracker = coordinator_fixture.TrackingUnitOfWorkFactory(self.database_url)
        opened = engine.lifecycle_with_ids("origin-run", "origin-open", "origin-claim").execute(
            ClaimAndOpenActivityRun("origin-request", engine.authority(), ExecutionLeaseDuration(600), IdempotencyKey("origin-claim")))
        fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("origin-start-event", "origin-start-action").execute(
            StartActivityRun("origin-run", engine.authority(), fence, IdempotencyKey("origin-start")))
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker, lambda _context, request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "origin-test"}))
        result = engine.coordinator(adapter).execute(replace(engine.command(generation=fence.generation,
            idempotency_key="origin-execute"), run_id="origin-run"))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["origin-runtime"])
        self.assertEqual(adapter.active_during_calls, [0])
        receipt_ids = iter(("origin-advance-event", "origin-advance-action"))
        self.configuration_origin = CurrentGraphAdvancementCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(receipt_ids)).execute(
                AdvanceCurrentGraph("workspace-a", "origin-run", "origin-plan", "graph-initial",
                    workspace.current_realized_projection_id, "graph-current", workspace.desired_realized_projection_id,
                    workspace.desired_graph_revision, engine.authority(), fence, IdempotencyKey("origin-advance")))
        with self.unit_of_work() as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.graph_id, current.projection_id, current.manifest_slot_count, current.bindings),
            ("complete", "graph-current", workspace.desired_realized_projection_id, 0, ()))
        self.assertEqual(self.protection_rows(), ([], []))

    def seed_truth(self, decision, *, history=None, approval_subject="activity-plan"):
        self.assertIs(decision, RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        self.assertEqual(history, "active-empty")
        self.assertEqual(approval_subject, "activity-plan")
        self.seeded_fence = ExecutionLeaseFence("worker-a", 7)
        existing = getattr(self, "configuration_existing_node", False)
        operation = ReconcileNode(NodeTarget("api")) if existing else StartNode(NodeTarget("api"))
        self.configuration_activity = PlannedActivity(ActivityId("start-api"), operation)
        activities = (self.configuration_activity,) + tuple(
            PlannedActivity(ActivityId(f"history-use-{index:03d}"), operation)
            for index in range(1, getattr(self, "configuration_history_count", 1)))
        product = _configuration_product()
        base = execution_graph("empty-runtime", runtime_id="docker")
        desired = _configuration_graph(product,
            selection=getattr(self, "configuration_selected_value", "approved-desired"))
        if existing:
            base = desired
        if existing:
            self.connection.execute("INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-a', 'Configuration preparation', 'created')")
        else:
            self.create_runtime_origin(base)
        with self.unit_of_work() as uow:
            stores = uow.stores
            self.configuration_product = stores.registered_products.register(
                workspace_id="workspace-a", descriptor_document=product.descriptor_document,
                source=product.source, imported_by=product.imported_by, imported_at=product.imported_at,
            )
            if existing:
                lineage = seed_identity_graphs(stores, workspace_id="workspace-a",
                    graph_ids=("graph-current", "graph-desired"),
                    graphs={"graph-current": base, "graph-desired": desired})
                # Defensive legacy/corrupt snapshot ONLY. The public pointer
                # owner correctly forbids configured current without accepted
                # provenance. This branch tests refusal, not valid bootstrap.
                stores.connection.execute("UPDATE cpk_workspaces SET current_graph_id=%s, "
                    "current_realized_projection_id=%s WHERE workspace_id=%s",
                    ("graph-current", lineage["graph-current"], "workspace-a"))
            else:
                stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-desired", workspace_id="workspace-a",
                    version=3, graph=desired, created_by="operator-a", created_at="2026-08-15T03:54:00Z"))
            workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            stores.activity_history.add_session(OperationSessionRecord(
                "session-a", "workspace-a", "operator-a", "Deploy configuration",
                OperationSessionStatus.OPEN, "2026-08-15T03:55:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord(
                "plan-a", "session-a", "graph-current", "graph-desired",
                ActivityPlanStatus.PLANNED, "2026-08-15T03:56:00Z",
                ActivityPlan(activities),
                base_realized_projection_id=workspace.current_realized_projection_id,
                desired_realized_projection_id=workspace.desired_realized_projection_id,
                desired_graph_revision=workspace.desired_graph_revision))
            stores.activity_history.add_approval_request(ApprovalRequestRecord(
                "approval-request-a", "session-a", ActivityPlanApprovalSubject("plan-a"),
                "operator-a", "2026-08-15T03:57:00Z", PolicyScope.PLAN_APPROVE, RiskLevel.LOW, False))
            stores.activity_history.add_approval_decision(ApprovalDecisionRecord(
                "approval-decision-a", "approval-request-a", "manager-a",
                ApprovalDecisionKind.APPROVED, PolicyScope.PLAN_APPROVE, "2026-08-15T03:58:00Z"))
            uow.commit()
        self.seed_execution_request()
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='claimed', claim_worker_id='worker-a', "
            "claim_generation=7, claimed_at='2098-01-01T00:00:00Z', "
            "lease_expires_at='2099-01-01T00:00:00Z' WHERE request_id='request-a'")
        with self.unit_of_work() as uow:
            uow.stores.execution._add_run(ActivityRunRecord(
                "run-a", "plan-a", AdmittedRun("request-a"), RetryIdentity(1),
                ActivityRunStatus.CLAIMED, "2026-08-15T03:59:10Z",
                metadata=BoundedEvidence.from_mapping({"attempt": 1})))
            for event in self.history_events("active-empty"):
                uow.stores.execution.add_event(event)
            uow.commit()

    def intent(self, **changes):
        self.assertEqual(changes, {}, "this fixture only owns its original new-node attempt")
        with self.unit_of_work() as uow:
            stores = uow.stores
            plan = stores.activity_history.get_plan("plan-a")
            context = replace(_context(activity=self.configuration_activity),
                request=stores.execution.get_request("request-a"),
                run=stores.execution.get_run("run-a"), plan_record=plan,
                base_graph=stores.realized_graphs.get(plan.base_realized_projection_id),
                desired_graph=stores.realized_graphs.get(plan.desired_realized_projection_id),
                registered_products=(self.configuration_product,), fence=self.seeded_fence)
        original = _runtime_effect_intent_for_context(context, self.configuration_activity)
        return replace(original, kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            configuration_instances=self.configuration_selection(original))

    def configuration_selection(self, original, *, attempt=1):
        # This is a proposal, not authority: the start owner must independently
        # rederive it from the pinned material and original attempt under L.
        refs = []
        for material in original.products:
            for artifact in material.product.runtime_contract.configuration_artifacts:
                birth = dict(workspace_id=original.source.workspace_id, run_id=original.source.run_id.value,
                    activity_id=original.activity_id.value,
                    attempt=attempt, runtime_id="docker", node_id="api", artifact_id=artifact.artifact_id,
                    target_path=artifact.target_path, media_type=artifact.media_type.value,
                    file_mode=artifact.file_mode.value, content_digest=artifact.content_digest)
                allocation_id = "cfg-" + sha256(
                    b"control-plane-kit.configuration-allocation-birth.v1\x00" + rfc8785.dumps(birth)
                ).hexdigest()
                refs.append(ConfigurationInstanceRef(allocation_id=allocation_id,
                    **{key: value for key, value in birth.items()
                       if key not in ("run_id", "activity_id", "attempt", "media_type", "file_mode")},
                    media_type=artifact.media_type, file_mode=artifact.file_mode))
        return ConfigurationInstanceSelection(tuple(refs))

    def configuration_command(self, intent=None):
        intent = self.intent() if intent is None else intent
        return self.command(intent=intent,
            transition=self.transition(identity=self.identity(activity_id="start-api"), intent=intent))

    def intent_for_attempt(self, *, compensation=False, run_id="run-a", activity_id="start-api"):
        # The established record fixture asks for a fingerprint while building
        # its state, before intent_attempt replaces it with the explicit source.
        # Preserve this world's exact original source, including historical uses.
        self.assertFalse(compensation, "configuration compensation is not a fixture authority")
        self.assertEqual(run_id, "run-a")
        return replace(self.intent(), activity_id=ActivityId(activity_id))

    def protection_rows(self):
        # Capability assertions make absent B1 behavior explicit, rather than
        # earning red from SQL against an absent table.
        for relation in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertIsNotNone(self.connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0],
                f"missing #1923 durable protection relation: {relation}")
        refs = self.connection.execute(
            "SELECT run_id, activity_id, attempt, artifact_id, workspace_id, allocation_id, "
            "runtime_id, node_id, ref_preimage, request_fingerprint, original_event_id, is_birth, "
            "birth_run_id, birth_activity_id, birth_attempt, birth_artifact_id, ref_digest "
            "FROM cpk_effect_configuration_refs ORDER BY artifact_id").fetchall()
        claims = self.connection.execute(
            "SELECT run_id, activity_id, attempt, artifact_id, workspace_id, allocation_id "
            "FROM cpk_configuration_claims ORDER BY artifact_id").fetchall()
        return refs, claims

    def complete_start_snapshot(self):
        """Include protective rows without making absent B1 schema a setup error."""
        protection = []
        for relation in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            if self.connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0] is None:
                protection.append((relation, None))
                continue
            rows = self.connection.execute(
                f"SELECT * FROM {relation} WHERE workspace_id='workspace-a' "
                "ORDER BY run_id, activity_id, attempt, artifact_id LIMIT 257").fetchall()
            self.assertLessEqual(len(rows), 256, "fixture snapshot exceeded its explicit bound")
            protection.append((relation, tuple(rows)))
        return self.attempt_snapshot(), tuple(protection)
