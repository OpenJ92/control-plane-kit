"""Independent cold/warm transport and pre-mutation peak admission evidence."""
from dataclasses import replace
import re
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_operations import configuration_preparation as values
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _configuration_accounting
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict, CoordinatorStatus
from control_plane_kit_operations import effect_attempt_start_interpreter as start_owner
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _joined_read, _active_read
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture


Footprint = values.ConfigurationEvidenceFootprint
PEAK = Footprint(66, 2103393, 627, 1)


def difference(after, before):
    return Footprint(*(getattr(after, field) - getattr(before, field)
        for field in ("records", "value_octets", "scalar_markers", "statements")))


def query_role(statement, _params=()):
    if " r FULL JOIN " in statement and "cpk_configuration_claims" in statement:
        return "pair"
    if statement.startswith("SELECT 1 FROM cpk_configuration_claim_transfers t "):
        return "transfer-anchor"
    if "FROM cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c " in statement:
        return "reciprocal-ref"
    if statement.lstrip().startswith("WITH original AS MATERIALIZED"):
        return "original-source"
    return "point"


def fixed_tail(count, transferred):
    # Existing fixed envelope plus 5P+3U per selected ref and four additional
    # T anchors per transferred birth outside the three measured cold passes.
    records, markers, statements = 24 * count + 264, 384 * count, 24 * count
    envelope = 192 * 1024 * count + 3 * 1024 * 1024 + 512 * 1024
    return Footprint(records, envelope - 128*records - 16*markers - 256*statements,
        markers, statements).plus(Footprint(13*count + 16*transferred,
            4278*count + 4*transferred, 58*count + 4*transferred, 8*count + 4*transferred))


class PostgresConfigurationTransferCapacityTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def measure_cold_and_warm(self):
        declared = []
        actual_query = _EvidenceRead.query
        def query(read, statement, params=(), **kwargs):
            role = query_role(statement)
            if role != "point":
                self.assertEqual(kwargs.get("identities", 1), {
                    "pair": 2, "transfer-anchor": 4, "reciprocal-ref": 2, "original-source": 3}[role])
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
                self.assertEqual(cold.records, sum(len(entry["widths"]) * {
                    "pair": 2, "transfer-anchor": 4, "reciprocal-ref": 2, "original-source": 3,
                    "point": 1}[entry["role"]] for entry in transported))
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
        cold = self.measure_cold_and_warm()
        actual_check = ConfigurationPreparationStore._require_current
        actual_proof = ConfigurationAcceptanceStore._accepted_transfer
        actual_capacity = values.configuration_preparation_capacity
        passes, admissions, ledgers, prefixes = [], [], [], []
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
            self.assertEqual(measured, cold)
            passes.append(readers[0])
            prefixes.append(accounting.used)
        def capacity(**kwargs):
            expected = fixed_tail(len(self.refs), len(self.refs)).plus(cold).plus(cold).plus(cold).plus(PEAK)
            self.assertEqual(kwargs["reserved_future"], expected)
            admissions.append(kwargs["current"])
            ledgers.append(_ACCOUNTING.get())
            if not tail:
                tail.update(prefix=kwargs["current"], forecast=expected, query_start=len(observed["queries"]))
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
        self.assertEqual(len(admissions), len(self.refs))
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
            forecast=tail["forecast"], raw_write_statements=raw_writes, query_roles=roles,
            maximum_peak_delta=tuple(max(entry["peak"][i] for entry in queries)
                - getattr(tail["prefix"], field) for i, field in enumerate(
                    ("records", "value_octets", "scalar_markers", "statements")))))
        self.assertEqual(delta.statements, physical.statements)
        for field in ("records", "value_octets", "scalar_markers", "statements"):
            self.assertGreaterEqual(getattr(delta, field), getattr(physical, field))
            self.assertLessEqual(getattr(delta, field), getattr(tail["forecast"], field))
        for entry in queries:
            peak = Footprint(*entry["peak"])
            self.assertLessEqual(peak.records, 4096)
            self.assertLessEqual(peak.accounted_bytes, 16 * 1024 * 1024)
            for field in ("records", "value_octets", "scalar_markers", "statements"):
                self.assertLessEqual(getattr(peak, field) - getattr(tail["prefix"], field),
                    getattr(tail["forecast"], field))

    def test_prior_plus_settled_suffix_fits_but_peak_refuses_before_start_mutation(self):
        cold = self.measure_cold_and_warm()
        suffix = fixed_tail(len(self.refs), len(self.refs)).plus(cold).plus(cold).plus(cold)
        command = self.carry.admit("reuse", "graph-reuse", ReconcileNode(NodeTarget("api")),
            graph=self.reuse.reuse_graph())
        harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness(self.base)
        before, calls = self.proof_snapshot(), []
        original = values.configuration_preparation_capacity
        def inject_prior(**kwargs):
            accounting = _ACCOUNTING.get()
            self.assertIsNotNone(accounting)
            self.assertEqual(kwargs["current"], accounting.used)
            extra = 16 * 1024 * 1024 - accounting.used.plus(suffix).accounted_bytes
            self.assertGreater(extra, 0)
            # Inject only extra prior bytes. Keep every real prefix charge and
            # pass through the real owner decision with its actual future.
            accounting.used = accounting.used.plus(Footprint(0, extra, 0, 0))
            self.assertEqual(accounting.used.plus(suffix).accounted_bytes, 16 * 1024 * 1024)
            self.assertGreater(accounting.used.plus(suffix).plus(PEAK).accounted_bytes, 16 * 1024 * 1024)
            calls.append(accounting.used)
            return original(**dict(kwargs, current=accounting.used))
        with mock.patch.object(values, "configuration_preparation_capacity", inject_prior), \
                mock.patch.object(start_owner, "_observation", side_effect=AssertionError(
                    "preflight refusal reached the post-preparation lease clock read")), \
                self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(replace(self.base.engine.command(generation=command.fence.generation,
                idempotency_key="execute-reuse"), run_id=command.run_id))
        self.assertEqual(len(calls), 1, "must reach the actual start capacity decision")
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(self.proof_snapshot(), before)
