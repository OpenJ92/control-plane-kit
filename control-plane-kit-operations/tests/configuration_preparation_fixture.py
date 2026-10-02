"""#1923 ordinary new-node preparation through the durable start boundary.

The graph and plan precede approval and real admission. Recorded run/lease
overlays only provide the existing start fixture's worker clock and fence.
There is no provider, accepted-current receipt, or configuration genesis here.
"""
from dataclasses import replace
from hashlib import sha256

import rfc8785

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef, ConfigurationInstanceSelection,
)
from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, NodeTarget, PlannedActivity, ReconcileNode, RiskLevel, StartNode,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ActivityRunRecord, AdmittedRun,
    ApprovalDecisionKind, ApprovalDecisionRecord, ApprovalRequestRecord, BoundedEvidence,
    OperationSessionRecord, OperationSessionStatus, RetryIdentity,
)
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.graph_lineage_fixture import execution_graph, seed_identity_graphs
from tests.postgres_effect_attempt_intent_store_fixture import PostgresEffectAttemptIntentStoreFixture
from tests.test_runtime_effect_translation import _configuration_graph, _configuration_product, _context


class ConfigurationPreparationFixture(PostgresEffectAttemptIntentStoreFixture):
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
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Configuration preparation', 'created')"
        )
        with self.unit_of_work() as uow:
            stores = uow.stores
            self.configuration_product = stores.registered_products.register(
                workspace_id="workspace-a", descriptor_document=product.descriptor_document,
                source=product.source, imported_by=product.imported_by, imported_at=product.imported_at,
            )
            lineage = seed_identity_graphs(stores, workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
                graphs={"graph-current": base, "graph-desired": desired})
            stores.workspaces.set_current_graph("workspace-a", "graph-current")
            stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            stores.activity_history.add_session(OperationSessionRecord(
                "session-a", "workspace-a", "operator-a", "Deploy configuration",
                OperationSessionStatus.OPEN, "2026-08-15T03:55:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord(
                "plan-a", "session-a", "graph-current", "graph-desired",
                ActivityPlanStatus.PLANNED, "2026-08-15T03:56:00Z",
                ActivityPlan(activities),
                base_realized_projection_id=lineage["graph-current"],
                desired_realized_projection_id=lineage["graph-desired"], desired_graph_revision=1))
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
