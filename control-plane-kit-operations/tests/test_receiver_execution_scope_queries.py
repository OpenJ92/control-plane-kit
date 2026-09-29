"""#1902 inspect actual reader SQL on PostgreSQL; no alternate query engine."""

import os
import unittest

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, RuntimeTarget, StartNode, StartRuntime
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture, SCOPES


class QueryRecorder:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return self.connection.execute(query, params)


def plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", ()):
        yield from plan_nodes(child)


class ReceiverExecutionScopeQueryTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def seed_fanout(self, *, groups, nodes, copies):
        runtimes = tuple(DockerRuntime(runtime_id="runtime-" + str(group), children=tuple(
            instantiate_product(self.document.product, f"node-{group}-{node}", ProductInstanceConfiguration())
            for node in range(nodes))) for group in range(groups))
        graph = compile_topology(DeploymentTopology("fanout", DockerRuntime(runtime_id="root", children=runtimes)))
        self.seed_graphs("fanout-base", self.empty_graph("base"), "fanout-desired", graph)
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_current_graph("workspace-a", "fanout-base")
            uow.stores.workspaces.set_desired_graph("workspace-a", "fanout-desired")
            uow.commit()
        for group in range(groups):
            plan = ActivityPlan(tuple(PlannedActivity(ActivityId(f"start-{group}-{node}"),
                StartNode(NodeTarget(f"node-{group}-{node}"))) for node in range(nodes)))
            self.seed_plan_truth(plan_id=f"fanout-plan-{group}", approval_request_id=f"fanout-approval-{group}",
                approval_decision_id=f"fanout-decision-{group}", plan=plan,
                base_graph_id="fanout-base", desired_graph_id="fanout-desired")
            for copy in range(copies):
                suffix = f"{group}-{copy}"
                self.admission_service("fanout-request-" + suffix, "fanout-action-" + suffix).execute(
                    self.command(plan_id=f"fanout-plan-{group}", approval_request_id=f"fanout-approval-{group}", key="fanout-" + suffix))

    def test_shared_4096_raw_rows_fit_across_prefixes_and_4097_refuse_before_dedup(self):
        module = self.require_scopes()
        self.seed_fanout(groups=4, nodes=128, copies=8)
        scopes = tuple(module.ExecutionReceiverScope(f"runtime-{group}", None) for group in range(4))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_receiver_scopes").fetchone()[0], 4096)
        result = self.evidence(*scopes)
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(len(result.request_ids), 32)
        extra = ActivityPlan((PlannedActivity(ActivityId("extra"), StartNode(NodeTarget("node-0-0"))),))
        self.seed_plan_truth(plan_id="extra-plan", approval_request_id="extra-approval", approval_decision_id="extra-decision",
            plan=extra, base_graph_id="fanout-base", desired_graph_id="fanout-desired")
        self.admission_service("extra-request", "extra-action").execute(
            self.command(plan_id="extra-plan", approval_request_id="extra-approval", key="extra"))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_receiver_scopes").fetchone()[0], 4097)
        self.assertEqual(self.evidence(*scopes).disposition, "capacity")

    def test_filled_transport_reduced_prefix_refuses_even_at_nominal_raw_row_cap(self):
        module = self.require_scopes()
        self.seed_fanout(groups=1, nodes=128, copies=32)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_receiver_scopes").fetchone()[0], 4096)
        # One 4097-row proof page cannot fit its 4096-byte-per-row reservations
        # in 16 MiB. A filled reduced LIMIT cannot establish exhaustion even
        # though the true retained population is within the nominal row cap.
        result = self.evidence(module.ExecutionReceiverScope("runtime-0", None))
        self.assertEqual(result.disposition, "capacity")

    def test_actual_node_and_runtime_queries_are_eligible_for_all_three_prefix_indexes(self):
        module = self.require_scopes()
        self.admit()
        self.admit_operations("runtime", StartRuntime(RuntimeTarget("docker")))
        recording = QueryRecorder(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
        indexes = set()
        with PostgresUnitOfWork(lambda: recording) as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            # Proves index eligibility and ordering, not a production latency
            # promise or a claim that tiny populations must choose an index.
            recording.connection.execute("SET LOCAL enable_seqscan=off")
            for scope in (module.ExecutionReceiverScope("docker", "app"),
                          module.ExecutionReceiverScope("docker", None)):
                recording.calls.clear()
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
                self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "conflict")
                selected = [(query, params) for query, params in recording.calls
                            if isinstance(query, str) and SCOPES in query and query.lstrip().upper().startswith(("SELECT", "WITH"))]
                self.assertTrue(selected)
                for query, params in selected:
                    explained = recording.connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + query, params).fetchone()[0][0]["Plan"]
                    nodes = tuple(plan_nodes(explained))
                    indexes.update(node["Index Name"] for node in nodes if "Index Name" in node)
                    for node in nodes:
                        if node["Node Type"] in ("Sort", "Incremental Sort"):
                            self.assertTrue(any(child["Node Type"] == "Limit" for child in plan_nodes(node)),
                                            "candidate sorting must follow a bounded prefix")
        self.assertTrue({SCOPES + suffix for suffix in (
            "_runtime_lookup", "_node_lookup", "_runtime_nodes_lookup")} <= indexes)

    def test_requested_scope_limit_deduplicates_before_capacity_and_refuses_1025(self):
        module = self.require_scopes()
        one = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(*(one for _ in range(1025))).disposition, "nonconflicting")
        scopes = tuple(module.ExecutionReceiverScope("docker", "node-" + str(i)) for i in range(1024))
        self.assertEqual(self.evidence(*scopes).disposition, "nonconflicting")
        result = self.evidence(*scopes, module.ExecutionReceiverScope("docker", "one-over"))
        self.assertEqual(result.disposition, "capacity")

    def test_foreign_workspace_guard_and_finished_guard_never_supply_evidence(self):
        module = self.require_scopes()
        scope = module.ExecutionReceiverScope("docker", "app")
        with self.unit_of_work() as first:
            guard = first.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.unit_of_work() as other:
                result = other.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
                self.assertEqual(module.classify_receiver_scope_evidence(result).disposition, "unavailable")
            result = first.stores.execution.receiver_scope_evidence("foreign", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(result).disposition, "unavailable")
            first.commit()
        with self.unit_of_work() as later:
            result = later.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(result).disposition, "unavailable")
