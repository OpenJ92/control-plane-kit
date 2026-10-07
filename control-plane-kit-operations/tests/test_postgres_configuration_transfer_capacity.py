"""Independent cold/warm transport and pre-mutation peak admission evidence."""
from dataclasses import replace
import re
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_operations import configuration_preparation as values
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _BOUND_ORDINARY_START, _configuration_accounting
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict, CoordinatorStatus
from control_plane_kit_operations import effect_attempt_start_interpreter as start_owner
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _joined_read, _active_read, _Unavailable, _Capacity
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture


Footprint = values.ConfigurationEvidenceFootprint
PEAK = Footprint(66, 2103393, 627, 1)
IDENTITIES = {"pair": 2, "transfer-anchor": 4, "cleanup-anchor": 3,
    "reciprocal-ref": 2, "original-source": 3, "origin-action": 2, "point": 1}


def difference(after, before):
    return Footprint(*(getattr(after, field) - getattr(before, field)
        for field in ("records", "value_octets", "scalar_markers", "statements")))


def query_role(statement, _params=()):
    if " r FULL JOIN " in statement and "cpk_configuration_claims" in statement:
        return "pair"
    if statement.startswith("SELECT 1 FROM cpk_configuration_claim_transfers t "):
        return "transfer-anchor"
    if statement.startswith("SELECT 1 FROM cpk_configuration_claim_closures c "):
        return "cleanup-anchor"
    if "FROM cpk_operation_actions a JOIN cpk_operation_sessions s " in statement:
        return "origin-action"
    if "FROM cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c " in statement:
        return "reciprocal-ref"
    if statement.lstrip().startswith("WITH original AS MATERIALIZED"):
        return "original-source"
    return "point"


class PostgresConfigurationTransferCapacityTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def reuse_command(self):
        admitted = self.carry.admit("reuse", "graph-reuse", ReconcileNode(NodeTarget("api")),
            graph=self.reuse.reuse_graph())
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
        command = replace(self.base.engine.command(generation=admitted.fence.generation,
            idempotency_key="execute-reuse"), run_id=admitted.run_id)
        return harness, command

    def test_bound_root_and_historical_selector_changes_refuse_before_proof_sql(self):
        from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _phase_columns
        harness, command = self.reuse_command()
        before, checked = self.proof_snapshot(), []
        actual_prepare, actual_query = ConfigurationPreparationStore._prepare, _EvidenceRead.query

        def prepared(store, *args, **kwargs):
            result = actual_prepare(store, *args, **kwargs)
            self.assertIsNotNone(_BOUND_ORDINARY_START.get())
            ref, queries = self.refs[0], []
            def query(read, statement, params=(), **options):
                queries.append(statement)
                return actual_query(read, statement, params, **options)
            with mock.patch.object(_EvidenceRead, "query", query):
                for key, revision in ((self.key(ref), self.revision + 1),
                        (("unknown-run", *self.key(ref)[1:]), self.revision)):
                    with self.assertRaises(_Unavailable):
                        store._prove_transferred_roots(result.stores.configuration_acceptance,
                            ((key, ref, revision),))
                with self.assertRaises(_Unavailable):
                    _phase_columns(store._connection, "header", (ref.workspace_id, self.revision + 1), ())
            self.assertEqual(queries, [], "closed roots/selectors must refuse before native proof SQL")
            checked.append(True)
            raise _Unavailable

        with mock.patch.object(ConfigurationPreparationStore, "_prepare", prepared), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(command)
        self.assertEqual(checked, [True])
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.proof_snapshot(), before)

    def test_one_bound_root_proves_the_complete_original_invocation_including_siblings(self):
        harness, command = self.reuse_command()
        before, checked = self.proof_snapshot(), []
        actual_prepare, actual_query = ConfigurationPreparationStore._prepare, _EvidenceRead.query

        def prepared(store, *args, **kwargs):
            result = actual_prepare(store, *args, **kwargs)
            pairs = []
            def query(read, statement, params=(), **options):
                if query_role(statement) == "pair":
                    self.assertEqual(tuple(params[5:9]), tuple(params[9:13]))
                    pairs.append(tuple(params[5:9]))
                return actual_query(read, statement, params, **options)
            ref = self.refs[0]
            with mock.patch.object(_EvidenceRead, "query", query):
                store._prove_transferred_roots(result.stores.configuration_acceptance,
                    ((self.key(ref), ref, self.revision),))
            self.assertGreater(len(self.refs), 1)
            self.assertEqual(pairs, [self.key(ref), *(self.key(sibling) for sibling in self.refs)])
            checked.append(True)
            raise _Unavailable

        with mock.patch.object(ConfigurationPreparationStore, "_prepare", prepared), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(command)
        self.assertEqual(checked, [True], "the real bound cold proof must reach every original sibling")
        self.assertEqual(self.proof_snapshot(), before)

    def test_historical_receipt_field_growth_hits_its_captured_transport_bound(self):
        harness, command = self.reuse_command()
        before, grew, refused = self.proof_snapshot(), [], []
        actual_prepare = ConfigurationPreparationStore._prepare
        actual_originals = ConfigurationAcceptanceStore._originals

        def prepared(store, *args, **kwargs):
            result = actual_prepare(store, *args, **kwargs)
            action = store._connection.execute("SELECT action_id FROM cpk_configuration_acceptances "
                "WHERE workspace_id=%s AND pinned_revision=%s", (self.refs[0].workspace_id, self.revision)).fetchone()[0]
            self.assertEqual(store._connection.execute("UPDATE cpk_operation_actions "
                "SET payload=payload || jsonb_build_object('transport_growth',repeat('x',4096)) "
                "WHERE action_id=%s", (action,)).rowcount, 1)
            grew.append(action)
            return result

        def originals(store, read, action, event):
            try:
                return actual_originals(store, read, action, event)
            except _Capacity:
                if grew:
                    refused.append(action)
                raise

        with mock.patch.object(ConfigurationPreparationStore, "_prepare", prepared), \
                mock.patch.object(ConfigurationAcceptanceStore, "_originals", originals), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(command)
        self.assertEqual(len(grew), 1)
        self.assertEqual(refused, grew, "missing-selector or semantic refusal cannot stand in for the field bound")
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.proof_snapshot(), before)

    def test_issued_cold_source_failure_retains_reservation_cleanup_and_owner_close(self):
        from control_plane_kit_operations.postgres.configuration_source import _SOURCE
        harness, command = self.reuse_command()
        before, state, forecasts = self.proof_snapshot(), {}, []
        actual_capacity = values.configuration_preparation_capacity
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=None, role_label=query_role)

        class FailingConnection(_PhaseConnection):
            def execute(connection, sql, params=()):
                if state.get("admitted"):
                    if sql == "SAVEPOINT cpk_configuration_source_read":
                        state["savepoint"] = _ACCOUNTING.get().used
                    if sql == _SOURCE:
                        state["failed"] = _ACCOUNTING.get().used
                        raise psycopg.DataError("injected cold source transport failure")
                return super().execute(sql, params)

        class MeasuredUnitOfWork(PostgresUnitOfWork):
            def __exit__(uow, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    state["end"] = observed["accounting"].used

        def factory():
            observed["accounting"] = _ACCOUNTING.get()
            return MeasuredUnitOfWork(lambda: FailingConnection(psycopg.connect(self.base.database_url), observed))

        def capacity(**kwargs):
            result = actual_capacity(**kwargs)
            self.assertIs(result, values.ConfigurationCapacityDecision.WITHIN_LIMITS)
            state.setdefault("prior", kwargs["current"])
            state.setdefault("query_start", len(observed["queries"]))
            forecasts.append(kwargs["reserved_future"])
            state["admitted"] = True
            return result

        with mock.patch.object(values, "configuration_preparation_capacity", capacity), \
                mock.patch.object(harness.start.inner, "_unit_of_work_factory", factory), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(command)
        self.assertIn("failed", state, "the admitted cold proof must reach source dispatch")
        reservation = difference(state["failed"], state["savepoint"])
        self.assertEqual(reservation, Footprint(3, 80032, 17, 1))
        cleanup = difference(state["end"], state["failed"])
        self.assertEqual((cleanup.records, cleanup.scalar_markers, cleanup.statements), (1, 1, 3))
        self.assertGreater(cleanup.value_octets, 0)
        self.assertLessEqual(cleanup.value_octets, 20)
        peak_bound = forecasts[1]
        peaks = [state["failed"], state["end"], *(Footprint(*entry["peak"])
            for entry in observed["queries"][state["query_start"]:])]
        for peak in peaks:
            self.assertLessEqual(peak.records, 4096)
            self.assertLessEqual(peak.accounted_bytes, 16 * 1024 * 1024)
            for field in ("records", "value_octets", "scalar_markers", "statements"):
                self.assertLessEqual(getattr(peak, field) - getattr(state["prior"], field),
                    getattr(peak_bound, field))
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.proof_snapshot(), before)

    def measure_cold_and_warm(self):
        declared = []
        actual_query = _EvidenceRead.query
        def query(read, statement, params=(), **kwargs):
            role = query_role(statement)
            if role != "point":
                self.assertEqual(kwargs.get("identities", 1), IDENTITIES[role])
            declared.append(Footprint(kwargs["records"] * kwargs.get("identities", 1),
                kwargs["octets"], kwargs["records"] * kwargs["cells"], 1))
            return actual_query(read, statement, params, **kwargs)
        with _configuration_accounting() as accounting:
            observed = dict(bytes=0, rows=0, largest_cell=0, statements=0,
                queries=[], accounting=accounting, role_label=query_role)
            factory = lambda: _PhaseConnection(psycopg.connect(self.base.database_url), observed)
            with PostgresUnitOfWork(factory) as uow, _joined_read(uow.stores.connection) as read:
                uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                self.assertEqual((read.sources, read.refs), ({}, {}))
                prior = read.used
                physical = dict(observed)
                offset = len(observed["queries"])
                with mock.patch.object(_EvidenceRead, "query", query):
                    for ref in self.refs:
                        uow.stores.configuration_acceptance._accepted_transfer(read, self.key(ref), ref, self.revision)
                cold = difference(read.used, prior)
                self.assertGreater(cold.records, 6 * len(self.refs))
                self.assertEqual(cold.statements, observed["statements"] - physical["statements"])
                self.assertGreaterEqual(cold.records, observed["rows"] - physical["rows"])
                self.assertGreaterEqual(cold.accounted_bytes, observed["bytes"] - physical["bytes"])
                transported = observed["queries"][offset:]
                self.assertEqual(cold.scalar_markers,
                    sum(len(widths) for entry in transported for widths in entry["widths"]))
                self.assertEqual(cold.records, sum(len(entry["widths"]) * IDENTITIES[entry["role"]]
                    for entry in transported))
                self.assertTrue({"pair", "transfer-anchor", "reciprocal-ref", "original-source"}
                    <= {entry["role"] for entry in transported})
                for reservation in declared:
                    for field in ("records", "value_octets", "scalar_markers", "statements"):
                        self.assertLessEqual(getattr(reservation, field), getattr(PEAK, field))
                for entry in observed["queries"][offset:]:
                    if not entry["widths"]:
                        self.assertIn(entry["sql"], ("SAVEPOINT cpk_configuration_source_read",
                            "RELEASE SAVEPOINT cpk_configuration_source_read"))
                    self.assertLessEqual(Footprint(*entry["peak"]).accounted_bytes, 16 * 1024 * 1024)
                warm_start = read.used
                warm_offset = len(observed["queries"])
                for ref in self.refs:
                    uow.stores.configuration_acceptance._accepted_transfer(read, self.key(ref), ref, self.revision)
                warm = difference(read.used, warm_start)
                # A memo hit still executes one P and one T, including all six
                # joined identities; no transfer decoder or cold source work.
                self.assertEqual((warm.records, warm.scalar_markers, warm.statements),
                    (6 * len(self.refs), 12 * len(self.refs), 2 * len(self.refs)))
                self.assertEqual(len(observed["queries"]) - warm_offset, warm.statements)
                self.assertLess(warm.accounted_bytes, cold.accounted_bytes)
                print("B1 transfer cold/warm", dict(cold=cold, warm=warm,
                    maximum_query_peak=max(Footprint(*entry["peak"]).accounted_bytes
                        for entry in observed["queries"][offset:])))
                return cold

    def test_actual_cold_transfer_work_and_warm_structural_checks(self):
        self.measure_cold_and_warm()

    def test_three_real_start_rechecks_use_fresh_cold_proofs_on_the_same_ledger(self):
        actual_check = ConfigurationPreparationStore._require_current
        actual_proof = ConfigurationAcceptanceStore._accepted_transfer
        actual_capacity = values.configuration_preparation_capacity
        passes, admissions, ledgers, prefixes, forecasts, cold_passes = [], [], [], [], [], []
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=None, role_label=query_role)
        tail = {}
        test = self

        class MeasuredUnitOfWork(PostgresUnitOfWork):
            def __exit__(uow, *args):
                super().__exit__(*args)
                if tail:
                    test.assertIs(_ACCOUNTING.get(), observed["accounting"])
                    tail["end"] = observed["accounting"].used
                    tail["query_end"] = len(observed["queries"])

        def factory():
            observed["accounting"] = _ACCOUNTING.get()
            self.assertIsNotNone(observed["accounting"])
            return MeasuredUnitOfWork(lambda: _PhaseConnection(psycopg.connect(self.base.database_url), observed))
        def check(store, prepared):
            proofs, readers = [], []
            accounting = _ACCOUNTING.get()
            self.assertTrue(ledgers, "issued check must follow capacity admission")
            self.assertIs(accounting, ledgers[0])
            before_check = accounting.used
            prior = prefixes[-1] if prefixes else admissions[-1]
            for field in ("records", "value_octets", "scalar_markers", "statements"):
                self.assertGreaterEqual(getattr(before_check, field), getattr(prior, field))
            def proof(owner, read, key, ref, revision):
                self.assertIs(read.accounting, accounting)
                self.assertIs(_active_read(store._connection), read,
                    "nested proof owners must use the fresh cold read")
                if not readers:
                    self.assertEqual((read.sources, read.refs), ({}, {}))
                readers.append(read)
                before = read.used
                result = actual_proof(owner, read, key, ref, revision)
                proofs.append((key, difference(read.used, before)))
                return result
            with mock.patch.object(ConfigurationAcceptanceStore, "_accepted_transfer", proof):
                actual_check(store, prepared)
            self.assertEqual([key for key, _ in proofs], [self.key(ref) for ref in self.refs])
            self.assertTrue(all(read is readers[0] for read in readers))
            measured = Footprint(0, 0, 0, 0)
            for _, used in proofs:
                measured = measured.plus(used)
            if cold_passes:
                self.assertEqual(measured, cold_passes[0])
            cold_passes.append(measured)
            passes.append(readers[0])
            prefixes.append(accounting.used)
        def capacity(**kwargs):
            self.assertEqual(kwargs["current"], _ACCOUNTING.get().used)
            admissions.append(kwargs["current"])
            forecasts.append(kwargs["reserved_future"])
            ledgers.append(_ACCOUNTING.get())
            if not tail:
                tail.update(prefix=kwargs["current"], query_start=len(observed["queries"]))
            return actual_capacity(**kwargs)
        command = self.carry.admit("reuse", "graph-reuse", ReconcileNode(NodeTarget("api")),
            graph=self.reuse.reuse_graph())
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
        with mock.patch.object(ConfigurationPreparationStore, "_require_current", check), \
                mock.patch.object(values, "configuration_preparation_capacity", capacity), \
                mock.patch.object(harness.start.inner, "_unit_of_work_factory", factory):
            result = harness.coordinator.execute(replace(self.base.engine.command(generation=command.fence.generation,
                idempotency_key="execute-reuse"), run_id=command.run_id))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(harness.adapter.runtime_calls), 1)
        self.assertEqual(len(passes), 3)
        self.assertEqual(len({id(read) for read in passes}), 3)
        self.assertEqual(len(admissions), 2 * len(self.refs))
        settled, peak_bound = forecasts[:2]
        self.assertEqual(forecasts, [settled, peak_bound] * len(self.refs))
        delta = difference(tail["end"], tail["prefix"])
        queries = observed["queries"][tail["query_start"]:tail["query_end"]]
        rows = [widths for entry in queries for widths in entry["widths"]]
        physical = Footprint(len(rows), sum(sum(widths) for widths in rows),
            sum(len(widths) for widths in rows), len(queries))
        raw_writes = {table: sum(entry["sql"].lstrip().startswith("INSERT INTO " + table + " ")
            for entry in queries) for table in ("cpk_effect_attempt_intents",
                "cpk_effect_configuration_refs", "cpk_configuration_claims")}
        self.assertEqual(tuple(raw_writes.values()), (1, len(self.refs), len(self.refs)))
        roles = {}
        for entry in queries:
            statement = entry["sql"].lstrip()
            role = entry["role"]
            if role == "point":
                relation = re.search(r"\b(?:FROM|INTO|UPDATE)\s+([a-z_]+)", statement)
                phase = ("length" if statement.startswith("SELECT octet_length(") else
                    "value" if statement.startswith("SELECT CASE WHEN") else statement.split()[0])
                role = phase + ":" + (relation.group(1) if relation else "scalar")
            roles[role] = roles.get(role, 0) + 1
        print("B1 start suffix", dict(settled=delta, physical=physical,
            settled_upper=settled, peak_upper=peak_bound, raw_write_statements=raw_writes, query_roles=roles,
            maximum_peak_delta=tuple(max(entry["peak"][i] for entry in queries)
                - getattr(tail["prefix"], field) for i, field in enumerate(
                    ("records", "value_octets", "scalar_markers", "statements")))))
        self.assertEqual(delta.statements, physical.statements)
        self.assertEqual(delta.value_octets, physical.value_octets)
        self.assertEqual(delta.scalar_markers, physical.scalar_markers)
        self.assertEqual(delta.records, sum(len(entry["widths"]) * IDENTITIES[entry["role"]]
            for entry in queries))
        for field in ("records", "value_octets", "scalar_markers", "statements"):
            self.assertLessEqual(getattr(delta, field), getattr(settled, field))
        for entry in queries:
            peak = Footprint(*entry["peak"])
            self.assertLessEqual(peak.records, 4096)
            self.assertLessEqual(peak.accounted_bytes, 16 * 1024 * 1024)
            for field in ("records", "value_octets", "scalar_markers", "statements"):
                self.assertLessEqual(getattr(peak, field) - getattr(tail["prefix"], field),
                    getattr(peak_bound, field))

    def test_prior_plus_settled_suffix_fits_but_peak_refuses_before_start_mutation(self):
        command = self.carry.admit("reuse", "graph-reuse", ReconcileNode(NodeTarget("api")),
            graph=self.reuse.reuse_graph())
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
        before, calls = self.proof_snapshot(), []
        original = values.configuration_preparation_capacity
        def inject_prior(**kwargs):
            accounting = _ACCOUNTING.get()
            self.assertIsNotNone(accounting)
            self.assertEqual(kwargs["current"], accounting.used)
            if not calls:
                extra = 16 * 1024 * 1024 - accounting.used.plus(kwargs["reserved_future"]).accounted_bytes
                self.assertGreater(extra, 0)
                # Preserve real prefix charges; add only synthetic prior bytes.
                accounting.used = accounting.used.plus(Footprint(0, extra, 0, 0))
                self.assertEqual(accounting.used.plus(kwargs["reserved_future"]).accounted_bytes, 16 * 1024 * 1024)
            decision = original(**dict(kwargs, current=accounting.used))
            calls.append(decision)
            return decision
        with mock.patch.object(values, "configuration_preparation_capacity", inject_prior), \
                mock.patch.object(start_owner, "_observation", side_effect=AssertionError(
                    "preflight refusal reached the post-preparation lease clock read")), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(replace(self.base.engine.command(generation=command.fence.generation,
                idempotency_key="execute-reuse"), run_id=command.run_id))
        self.assertEqual(calls, [values.ConfigurationCapacityDecision.WITHIN_LIMITS,
            values.ConfigurationCapacityDecision.BYTE_LIMIT], "S must fit and H must refuse at the actual owner")
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.proof_snapshot(), before)
