from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
import os
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.algebra import DeploymentTopology, ExternalRuntime
from control_plane_kit_core.planning import (
    ActivityPlan,
    DEFAULT_ACTIVITY_PLAN_CODEC,
    planning_scenarios,
    compile_activity_plan,
)
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorCodec,
    compile_topology,
    validate_graph,
    diff_graphs,
)
from control_plane_kit_operations import planning as planning_module
from control_plane_kit_operations.deployment_transitions import (
    Deploy,
    InitialDeployment,
    NoOpDeployment,
    TeardownDeployment,
    UpdateDeployment,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ActivityPlanningGraphStateConflict,
    ActivityPlanningResult,
    ActivityPlanningSessionConflict,
    DesiredGraphCommandService,
    RequestActivityPlan,
    SetDesiredGraph,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, OperationActionRecord,
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
    StartOperationSession,
)
from tests.runtime_management_fixtures import (
    management_graph, sdk_health_graph, registered_management_product,
    omitted_management_graph,
    bootstrap_management_graph,
)
from tests.test_plan_derivation import require_derivation


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        if not self._values:
            raise AssertionError("unexpected identity allocation")
        return self._values.pop(0)


class SentinelFailure(RuntimeError):
    pass


class ExplodingCodec(GraphDescriptorCodec):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure
        self.decode_calls = 0

    def decode(self, descriptor):
        self.decode_calls += 1
        raise self.failure


class MissingProjectionStore:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def get(self, projection_id: str):
        raise self.failure


class ProjectionStores:
    def __init__(self, realized_graphs) -> None:
        self.realized_graphs = realized_graphs


class ProjectionUnitOfWork:
    def __init__(self, realized_graphs) -> None:
        self.stores = ProjectionStores(realized_graphs)


class StaticProjectionStore:
    def __init__(self, record: RealizedGraphProjectionRecord) -> None:
        self.record = record

    def get(self, projection_id: str) -> RealizedGraphProjectionRecord:
        return self.record


class ReplayHistory:
    def __init__(
        self,
        *,
        action,
        plan=None,
        session=None,
        plan_failure: BaseException | None = None,
        session_failure: BaseException | None = None,
    ) -> None:
        self.action = action
        self.plan = plan
        self.session = session
        self.plan_failure = plan_failure
        self.session_failure = session_failure

    def lock_action_idempotency(self, session_id: str, key: str) -> None:
        return None

    def action_for_idempotency(self, session_id: str, key: str):
        return self.action

    def get_plan(self, plan_id: str):
        if self.plan_failure is not None:
            raise self.plan_failure
        return self.plan

    def get_session(self, session_id: str):
        if self.session_failure is not None:
            raise self.session_failure
        return self.session


class ReplayStores:
    def __init__(self, history: ReplayHistory) -> None:
        self.activity_history = history


class ReplayUnitOfWork:
    def __init__(self, history: ReplayHistory) -> None:
        self.stores = ReplayStores(history)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def commit(self) -> None:
        self.commits += 1


class PlanningTransitionReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run this test "
                "through the Docker-first Operations test apparatus"
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self._reset()

    def tearDown(self) -> None:
        try:
            self._reset()
        finally:
            self.connection.close()

    def _reset(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def operation_service(self, *ids: str) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-14T10:00:00Z",
            id_factory=Sequence(*ids),
        )

    def desired_service(self, *ids: str) -> DesiredGraphCommandService:
        return DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-14T10:01:00Z",
            id_factory=Sequence(*ids),
        )

    def planning_service(
        self,
        *ids: str,
        graph_codec: GraphDescriptorCodec | None = None,
        unit_of_work_factory=None,
    ) -> ActivityPlanningCommandService:
        arguments = {}
        if graph_codec is not None:
            arguments["graph_codec"] = graph_codec
        return ActivityPlanningCommandService(
            unit_of_work_factory or self.unit_of_work,
            clock=lambda: "2026-08-14T10:02:00Z",
            id_factory=Sequence(*ids),
            **arguments,
        )

    def scenario(self, scenario_id: str):
        return next(
            value
            for value in planning_scenarios()
            if value.scenario_id == scenario_id
        )

    def plan(
        self,
        current: DeploymentGraph,
        desired: DeploymentGraph,
    ) -> tuple[RequestActivityPlan, ActivityPlanningResult]:
        command = self.prepare(current, desired)
        return command, self.planning_service("plan-a", "action-plan").execute(
            command
        )

    def prepare(
        self,
        current: DeploymentGraph,
        desired: DeploymentGraph,
        *,
        registered_products=(),
    ) -> RequestActivityPlan:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            for product in registered_products:
                stores.registered_products.register(
                    workspace_id="workspace-a", descriptor_document=product.descriptor_document,
                    source=product.source, imported_by=product.imported_by,
                    imported_at=product.imported_at,
                )
            current_record = GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=current,
                created_by="operator-a",
                created_at="2026-08-14T09:59:00Z",
            )
            stores.graphs.save(current_record)
            stores.workspaces.set_current_graph("workspace-a", current_record.graph_id)
            unit_of_work.commit()

        self.operation_service("session-a", "action-start").execute(
            StartOperationSession(
                workspace_id="workspace-a",
                actor_id="operator-a",
                title="Plan transition replay",
                idempotency_key=IdempotencyKey("session"),
            )
        )
        desired_result = self.desired_service(
            "graph-desired", "action-desired"
        ).execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=desired,
                expected_desired_graph_id=None,
                expected_desired_realized_projection_id=None,
                expected_desired_graph_revision=0,
                idempotency_key=IdempotencyKey("desired"),
            )
        )
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        assert workspace.current_graph_id is not None
        assert workspace.current_realized_projection_id is not None
        command = RequestActivityPlan(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            expected_current_graph_id=workspace.current_graph_id,
            expected_desired_graph_id=desired_result.graph_version_id,
            expected_current_realized_projection_id=(
                workspace.current_realized_projection_id
            ),
            expected_desired_realized_projection_id=(
                desired_result.desired_realized_projection_id
            ),
            expected_desired_graph_revision=(
                desired_result.desired_graph_revision
            ),
            idempotency_key=IdempotencyKey("plan"),
        )
        return command

    def _store_historical_derivation(self, current, desired, *, profile=None, plan=None):
        command = self.prepare(current, desired)
        transition = Deploy(validate_graph(current), validate_graph(desired))
        arguments = {} if profile is None else {"derivation_profile": profile}
        record = ActivityPlanRecord(
            "historical-plan", "session-a", command.expected_current_graph_id,
            command.expected_desired_graph_id, ActivityPlanStatus.PLANNED,
            "2026-08-14T10:02:00Z", plan or compile_activity_plan(transition.diff),
            base_realized_projection_id=command.expected_current_realized_projection_id,
            desired_realized_projection_id=command.expected_desired_realized_projection_id,
            desired_graph_revision=command.expected_desired_graph_revision, **arguments,
        )
        payload = {
            "workspace_id": "workspace-a", "plan_id": record.plan_id,
            "base_graph_id": record.base_graph_id, "desired_graph_id": record.desired_graph_id,
            "base_realized_projection_id": record.base_realized_projection_id,
            "desired_realized_projection_id": record.desired_realized_projection_id,
            "desired_graph_revision": record.desired_graph_revision,
            "ready_for_execution": record.plan.ready_for_execution,
            "activity_count": len(record.plan.activities),
        }
        if profile is not None:
            payload["derivation_profile"] = profile.value
        with self.unit_of_work() as uow:
            uow.stores.activity_history.add_plan(record)
            action = OperationActionRecord(
                "historical-plan-action", "session-a", uow.stores.activity_history.next_action_ordinal("session-a"),
                OperatorCommandKind.REQUEST_ACTIVITY_PLAN, "operator-a", payload, "2026-08-14T10:02:00Z",
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=hashlib.sha256(json.dumps(command.descriptor(), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            )
            uow.stores.activity_history.add_action(action)
            uow.commit()
        return command, record, action

    def _move_pointer_and_close(self):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        self.desired_service("graph-moved", "action-moved").execute(SetDesiredGraph(
            "session-a", "workspace-a", "operator-a", DeploymentGraph("later"),
            workspace.desired_graph_id, IdempotencyKey("move"),
            expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
            expected_desired_graph_revision=workspace.desired_graph_revision,
        ))
        self.operation_service("action-close").execute(CloseOperationSession(
            "session-a", "operator-a", IdempotencyKey("close"),
        ))

    def test_manually_stored_managed_legacy_history_survives_pointer_move_and_close(self):
        from control_plane_kit_core.planning import compile_graph_activity_plan
        current, desired = DeploymentGraph("empty"), bootstrap_management_graph(self)
        command, record, action = self._store_historical_derivation(current, desired)
        self.assertNotEqual(record.plan, compile_graph_activity_plan(validate_graph(current), validate_graph(desired)))
        self._move_pointer_and_close()
        before = self._durable_counts()
        before_payload = self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s", (record.plan_id,)).fetchone()[0]
        replay = self.planning_service().execute(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, record)
        self.assertEqual(replay.action, action)
        self.assertEqual(replay.transition.current.graph, current)
        self.assertEqual(replay.transition.desired.graph, desired)
        self.assertEqual(self._durable_counts(), before)
        self.assertEqual(self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s", (record.plan_id,)).fetchone()[0], before_payload)

    def test_new_structural_profile_is_atomic_and_preserves_literal_request_fingerprint(self):
        module = require_derivation(self)
        command = replace(self.prepare(DeploymentGraph("empty"), DeploymentGraph("desired")),
                          expected_current_realized_projection_id=None,
                          expected_desired_realized_projection_id=None, expected_desired_graph_revision=None)
        expected = {"command": "request-activity-plan", "session_id": "session-a", "workspace_id": "workspace-a",
                    "actor_id": "operator-a", "expected_current_graph_id": "graph-current",
                    "expected_desired_graph_id": "graph-desired", "expected_current_realized_projection_id": None,
                    "expected_desired_realized_projection_id": None, "expected_desired_graph_revision": None,
                    "idempotency_key": "plan"}
        self.assertEqual(command.descriptor(), expected)
        result = self.planning_service("new-plan", "new-action").execute(command)
        self.assertIs(result.plan_record.derivation_profile, module.PlanDerivationProfile.STRUCTURAL_V1)
        self.assertEqual(result.action.payload["derivation_profile"], "structural-v1")
        self.assertEqual(result.action.intent_fingerprint, "8851850ecadc7112f83e231136192c1cf7724ffd1faa2be9bde3819fba6ffe66")
        self.assertEqual(result.descriptor()["derivation_profile"], "structural-v1")
        before = self._durable_counts()
        self.assertEqual(self.planning_service().execute(command).plan_record, result.plan_record)
        self.assertEqual(self._durable_counts(), before)

    def test_profiles_survive_every_store_reader_and_public_history_projection(self):
        module = require_derivation(self)
        from control_plane_kit_operations import InstanceReadService
        from control_plane_kit_operations.postgres import PostgresStoreBundle
        from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, SessionReadScope
        for profile in (None, *module.PlanDerivationProfile):
            with self.subTest(profile=profile):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                command, record, _ = self._store_historical_derivation(DeploymentGraph("empty"), DeploymentGraph("desired"), profile=profile)
                stores = PostgresStoreBundle(self.connection)
                request = ReadPageRequest(ReadCollection.SESSION_PLANS, SessionReadScope("workspace-a", "session-a"), 10)
                found = (stores.activity_history.get_plan(record.plan_id),
                         stores.activity_history.plans_for_session("session-a")[0],
                         stores.activity_history.plan_page(request).items[0],
                         stores.activity_history.overview_plans("workspace-a", record.desired_graph_id,
                             record.desired_realized_projection_id, record.desired_graph_revision)[0])
                self.assertTrue(all(value == record for value in found))
                self.assertTrue(all(value.derivation_profile is profile for value in found))
                service = InstanceReadService(workspace_store=stores.workspaces, graph_topology_store=stores.graphs,
                                              activity_history_store=stores.activity_history, execution_store=stores.execution)
                detail = service.plan_detail("workspace-a", record.plan_id).descriptor()["plan"]
                summary = service.session_plans(request).items[0]
                for value in (detail, summary):
                    self.assertEqual(value["payload"], DEFAULT_ACTIVITY_PLAN_CODEC.encode(record.plan))
                    if profile is None:
                        self.assertNotIn("derivation_profile", value)
                    else:
                        self.assertEqual(value["derivation_profile"], profile.value)
                self.assertEqual(self.planning_service().execute(command).plan_record, record)

    def test_profiled_graph_pair_replay_uses_stored_policy_after_pointer_change(self):
        module = require_derivation(self)
        from control_plane_kit_core.planning import compile_graph_activity_plan, ObserveManagementBootstrap
        current, desired = DeploymentGraph("empty"), bootstrap_management_graph(self)
        plan = compile_graph_activity_plan(validate_graph(current), validate_graph(desired))
        self.assertTrue(any(isinstance(value.operation, ObserveManagementBootstrap) for value in plan.activities))
        command, record, action = self._store_historical_derivation(current, desired,
            profile=module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1, plan=plan)
        self._move_pointer_and_close()
        before = self._durable_counts()
        replay = self.planning_service().execute(command)
        self.assertEqual(replay.plan_record, record)
        self.assertEqual(replay.action, action)
        self.assertEqual(replay.transition.desired.graph, desired)
        self.assertEqual(self._durable_counts(), before)

    def test_equal_plan_profile_tampering_fails_before_graph_decode_or_result_derivation(self):
        module = require_derivation(self)
        for profile in (None, *module.PlanDerivationProfile):
            for marker in ("absent", None, "unknown", "structural-v1", "management-graph-pair-v1"):
                if (profile is None and marker == "absent") or (profile is not None and marker == profile.value):
                    continue
                with self.subTest(profile=profile, marker=marker):
                    self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                    command, record, action = self._store_historical_derivation(DeploymentGraph("empty"), DeploymentGraph("desired"), profile=profile)
                    payload = dict(action.payload)
                    if marker == "absent":
                        payload.pop("derivation_profile", None)
                    else:
                        payload["derivation_profile"] = marker
                    self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s", (Jsonb(payload), action.action_id))
                    before = self._durable_counts()
                    codec = ExplodingCodec(SentinelFailure("must not decode mismatched evidence"))
                    with self.assertRaises(ActivityPlanningGraphStateConflict) as error:
                        self.planning_service(graph_codec=codec).execute(command)
                    self._assert_clean_error(error.exception)
                    self.assertEqual(codec.decode_calls, 0)
                    with self.assertRaises(InvalidOperationCommand):
                        ActivityPlanningResult(record, replace(action, payload=payload), Deploy(validate_graph(DeploymentGraph("empty")), validate_graph(DeploymentGraph("desired"))))
                    self.assertEqual(self._durable_counts(), before)

    def test_malformed_stored_envelope_is_bounded_through_all_readers_and_replay(self):
        module = require_derivation(self)
        from control_plane_kit_operations.postgres import PostgresStoreBundle
        from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, SessionReadScope
        command, record, _ = self._store_historical_derivation(DeploymentGraph("empty"), DeploymentGraph("desired"), profile=module.PlanDerivationProfile.STRUCTURAL_V1)
        payload = {"schema": "control-plane-kit.operations.activity-plan-record", "version": 1,
                   "derivation_profile": "STORED-PROFILE-CANARY", "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(record.plan)}
        self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s", (Jsonb(payload), record.plan_id))
        store = PostgresStoreBundle(self.connection).activity_history
        request = ReadPageRequest(ReadCollection.SESSION_PLANS, SessionReadScope("workspace-a", "session-a"), 10)
        reads = (lambda: store.get_plan(record.plan_id), lambda: store.plans_for_session("session-a"),
                 lambda: store.plan_page(request), lambda: store.overview_plans("workspace-a", record.desired_graph_id,
                     record.desired_realized_projection_id, record.desired_graph_revision))
        before = self._durable_counts()
        for read in reads:
            with self.assertRaises(module.PlanDerivationError) as error:
                read()
            self._assert_clean_error(error.exception, "CANARY")
        with self.assertRaises(ActivityPlanningGraphStateConflict) as error:
            self.planning_service().execute(command)
        self._assert_clean_error(error.exception, "CANARY")
        self.assertEqual(self._durable_counts(), before)

    def test_record_marker_change_or_removal_cannot_reclassify_equal_plan(self):
        module = require_derivation(self)
        profile = module.PlanDerivationProfile.STRUCTURAL_V1
        command, record, _ = self._store_historical_derivation(DeploymentGraph("empty"), DeploymentGraph("desired"), profile=profile)
        legacy = DEFAULT_ACTIVITY_PLAN_CODEC.encode(record.plan)
        changed = module.encode_stored_activity_plan(record.plan, profile=module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)
        for candidate in (legacy, changed):
            self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s", (Jsonb(candidate), record.plan_id))
            before = self._durable_counts()
            codec = ExplodingCodec(SentinelFailure("profile mismatch must precede graph decode"))
            with self.assertRaises(ActivityPlanningGraphStateConflict) as error:
                self.planning_service(graph_codec=codec).execute(command)
            self._assert_clean_error(error.exception)
            self.assertEqual(codec.decode_calls, 0)
            self.assertEqual(self._durable_counts(), before)

    def test_profiled_history_rejects_structural_and_observation_tampering(self):
        module = require_derivation(self)
        from control_plane_kit_core.planning import compile_graph_activity_plan, ObserveManagementBootstrap, PlanGraphSide, ManagementBootstrapStage
        current, desired = DeploymentGraph("empty"), bootstrap_management_graph(self)
        transition = Deploy(validate_graph(current), validate_graph(desired))
        plan = compile_graph_activity_plan(transition.current, transition.desired)
        command, record, _ = self._store_historical_derivation(current, desired,
            profile=module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1, plan=plan)
        observation = next(value for value in plan.activities if isinstance(value.operation, ObserveManagementBootstrap))
        operation = observation.operation
        alternatives = (
            replace(operation, target=replace(operation.target, graph_side=PlanGraphSide.BASE_GRAPH)),
            replace(operation, target=replace(operation.target, relation_digest="0" * 64)),
            replace(operation, stage=ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH
                if operation.stage is not ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH else ManagementBootstrapStage.GATEWAY_LOCAL_READY),
        )
        candidates = [compile_activity_plan(transition.diff)]
        candidates.extend(ActivityPlan(tuple(replace(value, operation=alternative) if value.activity_id == observation.activity_id else value
                                             for value in plan.activities)) for alternative in alternatives)
        dependent = next(value for value in plan.activities if isinstance(value.operation, ObserveManagementBootstrap) and value.dependencies)
        candidates.append(ActivityPlan(tuple(replace(value, dependencies=()) if value.activity_id == dependent.activity_id else value for value in plan.activities)))
        for candidate in candidates:
            self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s", (
                Jsonb(module.encode_stored_activity_plan(candidate, profile=record.derivation_profile)), record.plan_id))
            before = self._durable_counts()
            with self.assertRaises(ActivityPlanningGraphStateConflict) as error:
                self.planning_service().execute(command)
            self._assert_clean_error(error.exception)
            self.assertEqual(self._durable_counts(), before)

    def test_omitted_authored_declaration_cannot_hide_pinned_product_during_planning(self):
        for transit in (False, True):
            product = registered_management_product(transit=transit)
            graph = omitted_management_graph(product)
            for current, desired in ((DeploymentGraph("empty"), graph), (graph, DeploymentGraph("empty"))):
                with self.subTest(transit=transit, removing=not desired.nodes):
                    self._reset()
                    command = self.prepare(current, desired, registered_products=(product,))
                    before = self._durable_counts()
                    with self.assertRaises(InvalidOperationCommand):
                        self.planning_service("plan-a", "action-plan").execute(command)
                    self.assertEqual(self._durable_counts(), before)

    def test_unrelated_management_registration_does_not_block_plain_planning(self):
        product = registered_management_product()
        graph = omitted_management_graph(product)
        graph = replace(graph, nodes={"api": replace(graph.node("api"), metadata={})})
        command = self.prepare(DeploymentGraph("empty"), graph, registered_products=(product,))
        result = self.planning_service("plan-a", "action-plan").execute(command)
        self.assertTrue(result.plan_record.plan.activities)

    def test_variable_only_sdk_surface_is_conservatively_guarded(self):
        from control_plane_kit_core.algebra import BlockSpec
        from control_plane_kit_core.capabilities import CapabilityName
        from control_plane_kit_core.node_control import (
            ControlPlaneCommandCodec, ControlPlaneResultCodec, ControlPlaneStateCodec,
            ControlPlaneVariableDescriptor, ControlPlaneVariableKind,
            ControlPlaneVariableOperationContract, NodeControlGraphReference,
            NodeControlGraphReferenceRole, NodeControlOperation,
        )

        graph = sdk_health_graph()
        node = graph.node("api")
        variable = ControlPlaneVariableDescriptor(
            NodeControlGraphReference(NodeControlGraphReferenceRole.VARIABLE, "mode"),
            ControlPlaneVariableKind.SCALAR, ControlPlaneStateCodec.SCALAR_V1,
            (
                ControlPlaneVariableOperationContract(NodeControlOperation.READ_STATE, None, ControlPlaneResultCodec.STATE_V1),
                ControlPlaneVariableOperationContract(NodeControlOperation.APPLY_COMMAND, ControlPlaneCommandCodec.REPLACE_SCALAR_V1, ControlPlaneResultCodec.TRANSITION_V1),
            ),
        )
        surface = replace(node.block_spec.control_surfaces[0], variables=(variable,), health_reads=())
        graph = replace(graph, nodes={"api": replace(node, block_spec=BlockSpec(
            "api", capabilities=(CapabilityName.NODE_CONTROLLABLE,), control_surfaces=(surface,),
        ))})
        validate_graph(graph).require_valid()
        command = self.prepare(DeploymentGraph("empty"), graph)
        before = self._durable_counts()
        with self.assertRaises(InvalidOperationCommand):
            self.planning_service("plan-a", "action-plan").execute(command)
        self.assertEqual(self._durable_counts(), before)

    def test_omitted_declaration_pinned_product_equal_pair_remains_no_op(self):
        product = registered_management_product()
        graph = omitted_management_graph(product)
        command = self.prepare(graph, graph, registered_products=(product,))
        result = self.planning_service("plan-a", "action-plan").execute(command)
        self.assertEqual(result.plan_record.plan.activities, ())

    def test_new_sdk_health_intent_without_management_denies_before_plan_persistence(self):
        command = self.prepare(DeploymentGraph("empty"), sdk_health_graph())
        before = self._durable_counts()
        with self.assertRaises(InvalidOperationCommand):
            self.planning_service("plan-a", "action-plan").execute(command)
        self.assertEqual(self._durable_counts(), before)

    def test_absent_management_sdk_graph_no_op_and_historical_replay_remain_valid(self):
        graph = sdk_health_graph()
        command, first = self.plan(graph, graph)
        self.assertEqual(first.plan_record.plan.activities, ())
        before = self._durable_counts()
        replay = self.planning_service().execute(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, first.plan_record)
        self.assertEqual(replay.transition, first.transition)
        self.assertEqual(self._durable_counts(), before)

    def test_retained_sdk_surface_environment_change_cannot_use_diff_only_fallback(self):
        from control_plane_kit_core.environment import PublicStaticEnvironmentBinding

        current = sdk_health_graph()
        desired = sdk_health_graph(public_environment=(PublicStaticEnvironmentBinding("LABEL", "changed"),))
        command = self.prepare(current, desired)
        before = self._durable_counts()
        with self.assertRaises(InvalidOperationCommand):
            self.planning_service("plan-a", "action-plan").execute(command)
        self.assertEqual(self._durable_counts(), before)

    def test_explicit_management_or_transit_in_either_snapshot_denies_plain_work(self):
        for selected in (True, False):
            graph = management_graph(self, selected=selected)
            for current, desired in ((DeploymentGraph("empty"), graph), (graph, DeploymentGraph("empty"))):
                with self.subTest(selected=selected, removing=not desired.nodes):
                    self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                    command = self.prepare(current, desired)
                    before = self._durable_counts()
                    with self.assertRaises(InvalidOperationCommand):
                        self.planning_service("plan-a", "action-plan").execute(command)
                    self.assertEqual(self._durable_counts(), before)

    def test_equal_and_name_only_explicit_management_are_real_no_ops(self):
        graph = management_graph(self)
        for desired in (graph, replace(graph, name="renamed")):
            with self.subTest(name=desired.name):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                _, result = self.plan(graph, desired)
                self.assertEqual(result.plan_record.plan.activities, ())

    def test_nonempty_historical_sdk_plan_replays_without_new_admission(self):
        current, desired = DeploymentGraph("empty"), sdk_health_graph()
        command = self.prepare(current, desired)
        plan = compile_activity_plan(diff_graphs(validate_graph(current), validate_graph(desired)))
        self.assertTrue(plan.activities)
        record = ActivityPlanRecord(
            "historical-plan", "session-a", command.expected_current_graph_id,
            command.expected_desired_graph_id, ActivityPlanStatus.PLANNED,
            "2026-08-14T10:02:00Z", plan,
            base_realized_projection_id=command.expected_current_realized_projection_id,
            desired_realized_projection_id=command.expected_desired_realized_projection_id,
            desired_graph_revision=command.expected_desired_graph_revision,
        )
        payload = {
            "workspace_id": "workspace-a", "plan_id": record.plan_id,
            "base_graph_id": record.base_graph_id, "desired_graph_id": record.desired_graph_id,
            "base_realized_projection_id": record.base_realized_projection_id,
            "desired_realized_projection_id": record.desired_realized_projection_id,
            "desired_graph_revision": record.desired_graph_revision,
            "ready_for_execution": plan.ready_for_execution, "activity_count": len(plan.activities),
        }
        with self.unit_of_work() as uow:
            uow.stores.activity_history.add_plan(record)
            uow.stores.activity_history.add_action(OperationActionRecord(
                "historical-plan-action", "session-a", uow.stores.activity_history.next_action_ordinal("session-a"),
                OperatorCommandKind.REQUEST_ACTIVITY_PLAN, "operator-a", payload, "2026-08-14T10:02:00Z",
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=hashlib.sha256(json.dumps(command.descriptor(), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            ))
            uow.commit()
        before = self._durable_counts()
        replay = self.planning_service().execute(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, record)
        self.assertEqual(replay.transition.current.graph, current)
        self.assertEqual(replay.transition.desired.graph, desired)
        self.assertEqual(self._durable_counts(), before)

    def test_result_retains_exact_transition_without_descriptor_or_repr_material(
        self,
    ) -> None:
        scenario = self.scenario("fresh-deployment")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        transition_field = next(
            value for value in fields(ActivityPlanningResult) if value.name == "transition"
        )
        self.assertFalse(transition_field.repr)
        self.assertIsInstance(result.transition, InitialDeployment)
        self.assertEqual(
            result.plan_record.plan,
            compile_activity_plan(result.transition.diff),
        )
        self.assertNotIn(scenario.current_graph.name, repr(result))
        self.assertNotIn(scenario.desired_graph.name, repr(result))
        self.assertEqual(
            result.descriptor(),
            {
                "plan_id": result.plan_record.plan_id,
                "session_id": result.plan_record.session_id,
                "base_graph_id": result.plan_record.base_graph_id,
                "desired_graph_id": result.plan_record.desired_graph_id,
                "base_realized_projection_id": (
                    result.plan_record.base_realized_projection_id
                ),
                "desired_realized_projection_id": (
                    result.plan_record.desired_realized_projection_id
                ),
                "desired_graph_revision": (
                    result.plan_record.desired_graph_revision
                ),
                "ready_for_execution": (
                    result.plan_record.plan.ready_for_execution
                ),
                "activity_count": len(result.plan_record.plan.activities),
                "action_id": result.action.action_id,
                "action_ordinal": result.action.ordinal,
                "replayed": False,
            },
        )

    def test_equal_graph_values_under_distinct_ids_are_no_op(self) -> None:
        scenario = self.scenario("no-change")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        self.assertIsInstance(result.transition, NoOpDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertNotEqual(
            result.plan_record.base_graph_id,
            result.plan_record.desired_graph_id,
        )

    def test_distinct_empty_graph_names_are_zero_activity_update(self) -> None:
        _, result = self.plan(
            DeploymentGraph("empty-before"),
            DeploymentGraph("empty-after"),
        )

        self.assertIsInstance(result.transition, UpdateDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertFalse(result.transition.diff.empty)

    def test_external_runtime_addition_is_zero_activity_initial_deployment(
        self,
    ) -> None:
        desired = compile_topology(
            DeploymentTopology("external-runtime", ExternalRuntime())
        )
        _, result = self.plan(DeploymentGraph("empty"), desired)

        self.assertIsInstance(result.transition, InitialDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertFalse(result.transition.diff.empty)

    def test_scenario_matrix_returns_exact_transition_forms(self) -> None:
        cases = (
            ("fresh-deployment", InitialDeployment),
            ("backend-switch", UpdateDeployment),
            ("full-teardown", TeardownDeployment),
            ("no-change", NoOpDeployment),
        )
        for index, (scenario_id, expected_type) in enumerate(cases):
            with self.subTest(scenario_id=scenario_id):
                if index:
                    self._reset()
                scenario = self.scenario(scenario_id)
                _, result = self.plan(
                    scenario.current_graph,
                    scenario.desired_graph,
                )
                self.assertIsInstance(result.transition, expected_type)
                self.assertEqual(
                    result.plan_record.plan,
                    compile_activity_plan(result.transition.diff),
                )

    def test_review_blocked_plan_retains_update_transition(self) -> None:
        scenario = self.scenario("unsupported-implementation-transition")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        self.assertIsInstance(result.transition, UpdateDeployment)
        self.assertFalse(result.plan_record.plan.ready_for_execution)

    def test_new_service_replays_plan_pinned_transition_after_pointer_move(
        self,
    ) -> None:
        scenario = self.scenario("backend-switch")
        command, first = self.plan(scenario.current_graph, scenario.desired_graph)
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.desired_service("graph-moved", "action-moved").execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=DeploymentGraph("later-desired-pointer"),
                expected_desired_graph_id=workspace.desired_graph_id,
                expected_desired_realized_projection_id=(
                    workspace.desired_realized_projection_id
                ),
                expected_desired_graph_revision=workspace.desired_graph_revision,
                idempotency_key=IdempotencyKey("move-desired"),
            )
        )
        self.operation_service("action-close").execute(
            CloseOperationSession(
                session_id="session-a",
                actor_id="operator-a",
                idempotency_key=IdempotencyKey("close"),
            )
        )
        before = self._durable_counts()

        replay = self.planning_service().execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, first.plan_record)
        self.assertEqual(replay.transition, first.transition)
        self.assertEqual(self._durable_counts(), before)
        with self.unit_of_work() as unit_of_work:
            moved = unit_of_work.stores.workspaces.get("workspace-a")
        self.assertEqual(moved.desired_graph_id, "graph-moved")
        self.assertNotEqual(
            moved.desired_graph_id,
            replay.plan_record.desired_graph_id,
        )

    def test_workspace_evidence_conflict_precedes_projection_decode(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(payload, '{workspace_id}', '"workspace-b"')
            WHERE action_id = %s
            """,
            (result.action.action_id,),
        )
        codec = ExplodingCodec(AssertionError("codec must not run"))

        with self.assertRaises(ActivityPlanningGraphStateConflict) as captured:
            self.planning_service(graph_codec=codec).execute(command)

        self.assertEqual(str(captured.exception), "planning replay evidence is incongruent")
        self.assertEqual(codec.decode_calls, 0)
        self._assert_clean_error(captured.exception)

    def test_missing_plan_and_session_use_distinct_bounded_replay_categories(
        self,
    ) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(payload, '{plan_id}', '"MISSING-PLAN-CANARY"')
            WHERE action_id = %s
            """,
            (result.action.action_id,),
        )
        with self.assertRaises(ActivityPlanningGraphStateConflict) as missing_plan:
            self.planning_service().execute(command)
        self.assertEqual(str(missing_plan.exception), "planning replay truth is missing")
        self._assert_clean_error(missing_plan.exception, "MISSING-PLAN-CANARY")

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        history = ReplayHistory(
            action=result.action,
            plan=result.plan_record,
            session_failure=KeyError("MISSING-SESSION-CANARY"),
        )
        unit_of_work = ReplayUnitOfWork(history)
        with self.assertRaises(ActivityPlanningSessionConflict) as missing_session:
            self.planning_service(
                unit_of_work_factory=lambda: unit_of_work
            ).execute(command)
        self.assertEqual(
            str(missing_session.exception),
            "planning replay session is missing",
        )
        self._assert_clean_error(
            missing_session.exception,
            "MISSING-SESSION-CANARY",
        )
        self.assertEqual(unit_of_work.commits, 0)

    def test_foreign_projection_membership_is_bounded_before_graph_use(self) -> None:
        foreign = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord.from_graph(
                graph_id="graph-a",
                workspace_id="workspace-b",
                version=1,
                graph=DeploymentGraph("FOREIGN-GRAPH-CANARY"),
                created_by="operator-b",
                created_at="2026-08-14T10:00:00Z",
            )
        )

        with self.assertRaises(ActivityPlanningGraphStateConflict) as captured:
            planning_module._projection_record(
                ProjectionUnitOfWork(StaticProjectionStore(foreign)),
                foreign.projection_id,
                "graph-a",
                "workspace-a",
            )

        self.assertEqual(str(captured.exception), "realized graph truth is unavailable")
        self._assert_clean_error(
            captured.exception,
            "workspace-b",
            "FOREIGN-GRAPH-CANARY",
        )

    def test_first_planning_translates_malformed_projection_cause_free(self) -> None:
        scenario = self.scenario("backend-switch")
        command = self.prepare(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = '{"name":"FIRST-GRAPH-CANARY","nodes":"bad"}'::jsonb
            WHERE projection_id = %s
            """,
            (command.expected_desired_realized_projection_id,),
        )

        with self.assertRaises(ActivityPlanningGraphInvalid) as captured:
            self.planning_service("plan-a", "action-plan").execute(command)

        self.assertEqual(str(captured.exception), "persisted graph pair is invalid")
        self._assert_clean_error(captured.exception, "FIRST-GRAPH-CANARY")

    def test_replay_rejects_malformed_graph_and_plan_incongruence(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = '{"name":"GRAPH-CANARY","nodes":"bad"}'::jsonb
            WHERE projection_id = %s
            """,
            (result.plan_record.desired_realized_projection_id,),
        )
        with self.assertRaises(ActivityPlanningGraphInvalid) as malformed:
            self.planning_service().execute(command)
        self.assertEqual(str(malformed.exception), "persisted graph pair is invalid")
        self._assert_clean_error(malformed.exception, "GRAPH-CANARY")

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            "UPDATE cpk_activity_plans SET payload = %s WHERE plan_id = %s",
            (
                Jsonb(DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(()))),
                result.plan_record.plan_id,
            ),
        )
        with self.assertRaises(ActivityPlanningGraphStateConflict) as mismatch:
            self.planning_service().execute(command)
        self.assertEqual(
            str(mismatch.exception),
            "persisted plan does not match graph transition",
        )
        self._assert_clean_error(mismatch.exception)

    def test_replay_rejects_decodable_validation_invalid_graph(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        connected = self.scenario("insert-rate-limiter").desired_graph
        invalid = replace(connected, edges={})
        self.assertFalse(validate_graph(invalid).valid)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = %s
            WHERE projection_id = %s
            """,
            (
                Jsonb(DEFAULT_GRAPH_CODEC.encode(invalid)),
                result.plan_record.desired_realized_projection_id,
            ),
        )

        with self.assertRaises(ActivityPlanningGraphInvalid) as captured:
            self.planning_service().execute(command)

        self.assertEqual(str(captured.exception), "persisted graph pair is invalid")
        self._assert_clean_error(captured.exception, invalid.name)

    def test_unexpected_codec_failure_escapes_replay_by_identity(self) -> None:
        scenario = self.scenario("backend-switch")
        command, _ = self.plan(scenario.current_graph, scenario.desired_graph)
        sentinel = SentinelFailure("unexpected-codec-failure")
        codec = ExplodingCodec(sentinel)

        with self.assertRaises(SentinelFailure) as captured:
            self.planning_service(graph_codec=codec).execute(command)

        self.assertIs(captured.exception, sentinel)
        self.assertEqual(codec.decode_calls, 1)

    def test_shared_projection_lookup_is_bounded_and_unexpected_errors_escape(
        self,
    ) -> None:
        with self.assertRaises(ActivityPlanningGraphStateConflict) as missing:
            planning_module._projection_record(
                ProjectionUnitOfWork(
                    MissingProjectionStore(KeyError("STORE-CANARY"))
                ),
                "PROJECTION-CANARY",
                "graph-a",
                "workspace-a",
            )
        self.assertEqual(str(missing.exception), "realized graph truth is unavailable")
        self._assert_clean_error(
            missing.exception,
            "STORE-CANARY",
            "PROJECTION-CANARY",
        )

        sentinel = SentinelFailure("unexpected-store-failure")
        with self.assertRaises(SentinelFailure) as unexpected:
            planning_module._projection_record(
                ProjectionUnitOfWork(MissingProjectionStore(sentinel)),
                "projection-a",
                "graph-a",
                "workspace-a",
            )
        self.assertIs(unexpected.exception, sentinel)

    def test_result_rejects_transition_whose_compiled_plan_is_incongruent(
        self,
    ) -> None:
        scenario = self.scenario("fresh-deployment")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)
        same = validate_graph(DeploymentGraph("same"))
        wrong_transition = Deploy(same, same)

        with self.assertRaises(TypeError):
            ActivityPlanningResult(result.plan_record, result.action)
        with self.assertRaises(InvalidOperationCommand):
            ActivityPlanningResult(result.plan_record, result.action, object())
        with self.assertRaises(InvalidOperationCommand):
            ActivityPlanningResult(
                result.plan_record,
                result.action,
                wrong_transition,
            )

    def test_first_and_replayed_results_preserve_the_same_declared_transition(self):
        module = require_derivation(self)
        scenario = self.scenario("backend-switch")
        command, first = self.plan(scenario.current_graph, scenario.desired_graph)
        replay = self.planning_service().execute(command)
        expected = Deploy(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph))
        for result in (first, replay):
            self.assertEqual(result.transition, expected)
            self.assertIs(result.plan_record.derivation_profile, module.PlanDerivationProfile.STRUCTURAL_V1)
            self.assertEqual(result.plan_record.plan, module.derive_activity_plan(expected, profile=result.plan_record.derivation_profile))
        self.assertEqual(first.plan_record, replay.plan_record)

    def _durable_counts(self) -> tuple[int, ...]:
        return tuple(
            self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "cpk_graph_versions",
                "cpk_realized_graph_projections",
                "cpk_activity_plans",
                "cpk_operation_actions",
            )
        )

    def _assert_clean_error(
        self,
        error: BaseException,
        *canaries: str,
    ) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for canary in canaries:
            self.assertNotIn(canary, str(error))


if __name__ == "__main__":
    unittest.main()
