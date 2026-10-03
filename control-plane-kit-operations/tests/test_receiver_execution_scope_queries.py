"""#1902 inspect actual reader SQL on PostgreSQL; no alternate query engine."""

import os
import unittest

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, RuntimeTarget, StartNode, StartRuntime
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
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


def plan_paths(plan, parents=()):
    yield plan, parents
    for child in plan.get("Plans", ()):
        yield from plan_paths(child, (*parents, plan))


class ReceiverExecutionScopeQueryTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_shared_accounting_reads_small_runtime_wide_candidates_after_prior_transport(self):
        module = self.require_scopes()
        self.admit()
        with _configuration_accounting("candidate-query"):
            recording = QueryRecorder(self.connection)
            read = _EvidenceRead(recording)
            read.query("SELECT 'prior'::text", (), records=1, octets=5, cells=1)
            before = read.used
            found = _ExecutionScopeStorage(recording, read).candidates(
                "workspace-a", (module.ExecutionReceiverScope("docker", None),))
            self.assertEqual(found, ("execution-a",))
            self.assertGreater(read.used.records, before.records)
            self.assertGreater(read.used.accounted_bytes, before.accounted_bytes)
            self.assertLessEqual(read.used.records, 4096)
            self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)

    def test_shared_exhausted_record_or_byte_allowance_refuses_before_candidate_transport(self):
        module = self.require_scopes()
        self.admit()
        # Explicit local ledger boundaries, not evidence of a naturally huge
        # application history. Actual candidate reads still use PostgreSQL.
        for used in (ConfigurationEvidenceFootprint(4096, 0, 0, 0),
                     ConfigurationEvidenceFootprint(0, 16 * 1024 * 1024, 0, 0)):
            with self.subTest(used=used), _configuration_accounting("candidate-query"):
                recording = QueryRecorder(self.connection)
                read = _EvidenceRead(recording)
                read.used = used
                with self.assertRaises(module.ReceiverScopeCapacity):
                    _ExecutionScopeStorage(recording, read).candidates(
                        "workspace-a", (module.ExecutionReceiverScope("docker", None),))
                self.assertEqual(recording.calls, [])
                self.assertEqual(read.used, used)

    def test_shared_full_shortened_prefix_refuses_before_duplicate_request_deduplication(self):
        module = self.require_scopes()
        self.seed_fanout(groups=1, nodes=2, copies=1)
        # Two scope rows share one real admitted request. Both independent
        # reservation ceilings shorten the page to two, which cannot prove
        # exhaustion even though deduplication would yield only one request.
        for used in (ConfigurationEvidenceFootprint(4094, 0, 0, 0),
                     ConfigurationEvidenceFootprint(0, 16 * 1024 * 1024 - 8800, 0, 0)):
            with self.subTest(used=used), _configuration_accounting("candidate-query"):
                recording = QueryRecorder(self.connection)
                read = _EvidenceRead(recording)
                read.used = used
                with self.assertRaises(module.ReceiverScopeCapacity):
                    _ExecutionScopeStorage(recording, read).candidates(
                        "workspace-a", (module.ExecutionReceiverScope("runtime-0", None),))
                self.assertEqual(len(recording.calls), 1)
                self.assertEqual(recording.calls[0][1][-1], 2)
                self.assertEqual(read.used.records, used.records + 2)
                self.assertLessEqual(read.used.records, 4096)
                self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)

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
            for copy in range(copies):
                suffix = f"{group}-{copy}"
                self.seed_plan_truth(plan_id="fanout-plan-" + suffix, approval_request_id="fanout-approval-" + suffix,
                    approval_decision_id="fanout-decision-" + suffix, plan=plan,
                    base_graph_id="fanout-base", desired_graph_id="fanout-desired")
                self.admission_service("fanout-request-" + suffix, "fanout-action-" + suffix).execute(
                    self.command(plan_id="fanout-plan-" + suffix, approval_request_id="fanout-approval-" + suffix, key="fanout-" + suffix))

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

    def test_actual_queries_cap_all_three_indexed_prefix_kinds_before_relational_processing(self):
        module = self.require_scopes()
        # Representative retained history; optimizer choice among compatible
        # overlapping indexes is not an application invariant.
        self.seed_fanout(groups=1, nodes=64, copies=16)
        self.seed_plan_truth(plan_id="runtime-plan", approval_request_id="runtime-approval",
            approval_decision_id="runtime-decision", plan=ActivityPlan((
                PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("runtime-0"))),)),
            base_graph_id="fanout-base", desired_graph_id="fanout-desired")
        self.admission_service("runtime-request", "runtime-action").execute(
            self.command(plan_id="runtime-plan", approval_request_id="runtime-approval", key="runtime"))
        self.connection.execute("ANALYZE cpk_execution_receiver_scopes")
        recording = QueryRecorder(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
        proven_prefixes = set()
        candidate_plans = []
        prefix_tails = {
            "workspace_id=%s AND runtime_id=%s AND scope_kind='runtime' LIMIT %s": "runtime",
            "workspace_id=%s AND runtime_id=%s AND scope_kind='node' AND node_id=%s LIMIT %s": "node-point",
            "workspace_id=%s AND runtime_id=%s AND scope_kind='node' LIMIT %s": "all-nodes",
        }
        with PostgresUnitOfWork(lambda: recording) as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            # Proves prefix eligibility and a cap before relational processing,
            # not a bound on bitmap construction or index/heap work and latency.
            recording.connection.execute("SET LOCAL enable_seqscan=off")
            for scope in (module.ExecutionReceiverScope("runtime-0", "node-0-0"),
                          module.ExecutionReceiverScope("runtime-0", None)):
                recording.calls.clear()
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
                self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "conflict")
                selected = [(query, params) for query, params in recording.calls
                            if isinstance(query, str) and SCOPES in query and "scope_kind=" in query
                            and query.lstrip().upper().startswith(("SELECT", "WITH"))]
                self.assertEqual(len(selected), 2, {"scope": scope, "queries": selected})
                observed_prefixes = []
                for query, params in selected:
                    explained = recording.connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + query, params).fetchone()[0][0]["Plan"]
                    diagnostic = {"fixture_query": query, "fixture_params": params, "plan": explained}
                    candidate_plans.append(diagnostic)
                    tail = query.partition(" WHERE ")[2]
                    self.assertIn(tail, prefix_tails, diagnostic)
                    prefix = prefix_tails[tail]
                    observed_prefixes.append(prefix)
                    expected_params = ("workspace-a", "runtime-0")
                    if prefix == "node-point":
                        expected_params += ("node-0-0",)
                    self.assertEqual(params[:-1], expected_params, diagnostic)
                    self.assertIs(type(params[-1]), int, diagnostic)
                    self.assertGreater(params[-1], 0, diagnostic)
                    self.assertLessEqual(params[-1], 4097 if prefix == "all-nodes" else 65, diagnostic)
                    compatible_indexes = {SCOPES + "_runtime_lookup"} if prefix == "runtime" else {
                        SCOPES + "_node_lookup", SCOPES + "_runtime_nodes_lookup"}
                    compatible_paths = 0
                    for node, parents in plan_paths(explained):
                        if node.get("Index Name") not in compatible_indexes:
                            continue
                        path_diagnostic = {**diagnostic, "index": node["Index Name"],
                            "ancestors": tuple(parent["Node Type"] for parent in parents)}
                        condition = node.get("Index Cond", "")
                        self.assertIn("workspace_id", condition, path_diagnostic)
                        self.assertIn("runtime_id", condition, path_diagnostic)
                        if prefix == "node-point":
                            self.assertIn("node_id", condition, path_diagnostic)
                        capped = False
                        for parent in reversed(parents):
                            if parent["Node Type"] == "Limit":
                                capped = True
                                break
                            self.assertNotIn(parent["Node Type"], (
                                "Sort", "Incremental Sort", "Unique", "Aggregate", "GroupAggregate",
                                "HashAggregate", "Nested Loop", "Hash Join", "Merge Join", "Append",
                                "Merge Append", "SetOp", "ProjectSet"),
                                {
                                    "law": "each candidate prefix must be capped before combination/deduplication/sorting",
                                    **path_diagnostic,
                                })
                        self.assertTrue(capped, path_diagnostic)
                        compatible_paths += 1
                    self.assertGreater(compatible_paths, 0, diagnostic)
                    proven_prefixes.add(prefix)
                self.assertCountEqual(observed_prefixes,
                    ("runtime", "all-nodes" if scope.node_id is None else "node-point"), candidate_plans)
        self.assertEqual(proven_prefixes, {"runtime", "node-point", "all-nodes"}, candidate_plans)

    def test_requested_scope_limit_deduplicates_before_capacity_and_refuses_1025(self):
        module = self.require_scopes()
        one = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(*(one for _ in range(1025))).disposition, "nonconflicting")
        scopes = tuple(module.ExecutionReceiverScope("docker", "node-" + str(i)) for i in range(1024))
        self.assertEqual(self.evidence(*scopes).disposition, "nonconflicting")
        result = self.evidence(*scopes, module.ExecutionReceiverScope("docker", "one-over"))
        self.assertEqual(result.disposition, "capacity")

    def test_transported_reference_bound_uses_utf8_without_truncating_scope(self):
        module = self.require_scopes()
        scope = module.ExecutionReceiverScope("é" * 1024, None)
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")
        with self.assertRaises(module.ReceiverScopeCapacity):
            module.ExecutionReceiverScope("é" * 1024 + "x", None)

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
