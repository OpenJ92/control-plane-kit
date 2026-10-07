"""Recorded B1 dispositions through real publication and cold-reader owners."""
from copy import deepcopy
from hashlib import sha256
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.planning import RuntimeTarget, StartRuntime
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection
from tests.test_postgres_configuration_transfer_capacity import Footprint, difference, query_role, IDENTITIES


def observation():
    return dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
        accounting=None, role_label=query_role)


class PublicationWire(_PhaseConnection):
    def __init__(self, connection, observed, *, cold=False):
        super().__init__(connection, observed)
        self.cold = cold

    def execute(self, *args, **kwargs):
        current = _ACCOUNTING.get()
        if self.cold and current is not None and self.observations["accounting"] is None:
            self.observations["accounting"] = current
        if current is not None and current is self.observations["accounting"]:
            return super().execute(*args, **kwargs)
        return self.connection.execute(*args, **kwargs)


def within(case, actual, bound):
    for field in ("records", "value_octets", "scalar_markers", "statements"):
        case.assertLessEqual(getattr(actual, field), getattr(bound, field), field)


def reconciles(case, used, observed):
    rows = [(entry, widths) for entry in observed["queries"] for widths in entry["widths"]]
    case.assertEqual(used.records, sum(IDENTITIES[entry["role"]] for entry, _ in rows))
    case.assertEqual(used.value_octets, sum(sum(widths) for _, widths in rows))
    case.assertEqual(used.scalar_markers, sum(len(widths) for _, widths in rows))
    case.assertEqual(used.statements, len(observed["queries"]))


class PostgresConfigurationTransferPublicationTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def carry_command(self):
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        return self.carry.prepare("bounded-transfer", "graph-bounded-transfer",
            StartRuntime(RuntimeTarget("runtime-b")),
            graph=self.carry.graph.add_runtime(extra.runtimes["runtime-b"]))

    def advance(self, command, factory=None):
        return CurrentGraphAdvancementCommandService(factory or self.base.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=iter(("event-bounded-transfer", "action-bounded-transfer")).__next__).execute(command)

    def measure_fit(self, *, reused):
        command = self.reuse.execute_reuse()[0] if reused else self.carry_command()
        before, observed, state = self.transfer_snapshot(), observation(), {}
        preflight, commit, current = (ConfigurationAcceptanceStore._preflight,
            PostgresUnitOfWork.commit, ConfigurationAcceptanceStore._require_current)
        checks = []

        def admit(store, prepared):
            prior = prepared.evidence_read.used
            caches = deepcopy((prepared.evidence_read.refs, prepared.evidence_read.sources))
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("forecast issued SQL")):
                snapshot, future, publication = store._publication_budgets(prepared)
            self.assertEqual(prepared.evidence_read.used, prior)
            self.assertEqual((prepared.evidence_read.refs, prepared.evidence_read.sources), caches)
            result = preflight(store, prepared)
            self.assertEqual(prepared.evidence_read.used, prior)
            transferred = {self.key(ref) for ref in self.refs}
            keys = tuple(tuple(dict.fromkeys((tuple(slot[3:7]), tuple(slot[7:11])))) for slot in prepared.slots)
            self.assertEqual((len(keys), sum(map(len, keys)), sum(key in transferred for slot in keys for key in slot)),
                (4, 6 if reused else 4, 2))
            observed["accounting"] = _ACCOUNTING.get()
            state.update(prior=prior, snapshot=snapshot, future=future, publication=publication)
            return result

        def check(store, prepared):
            checks.append(prepared.plan.plan_id)
            return current(store, prepared)

        def committed(uow):
            if observed["accounting"] is not None and _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)

        factory = lambda: PostgresUnitOfWork(lambda: PublicationWire(psycopg.connect(self.base.database_url), observed))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), \
                mock.patch.object(ConfigurationAcceptanceStore, "_require_current", check), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            accepted = self.advance(command, factory)
        self.assertEqual(checks, [command.plan_id] * 4)
        self.assertFalse(accepted.replayed)
        self.assertEqual(self.transfer_snapshot(), before)
        used = difference(state["end"], state["prior"])
        within(self, used, state["publication"].settled)
        for entry in observed["queries"]:
            within(self, difference(Footprint(*entry["peak"]), state["prior"]), state["publication"].peak)
        reconciles(self, used, observed)

        cold, snapshots = observation(), []
        manifest = ConfigurationAcceptanceStore._receipt_manifest
        def measured_manifest(store, workspace, revision, read):
            start = read.used
            result = manifest(store, workspace, revision, read)
            snapshots.append(difference(read.used, start))
            return result
        with mock.patch.object(ConfigurationAcceptanceStore, "_receipt_manifest", measured_manifest):
            with PostgresUnitOfWork(lambda: PublicationWire(
                    psycopg.connect(self.base.database_url), cold, cold=True)) as uow:
                result = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual(result.state, "complete")
        self.assertEqual((result.workspace_id, result.graph_id, result.projection_id,
            result.pinned_revision, result.manifest_slot_count),
            ("workspace-a", accepted.to_authored_graph_id, accepted.to_realized_projection_id,
             accepted.desired_graph_revision, 4))
        self.assertEqual(tuple(binding.ref for binding in result.bindings), self.membership.refs)
        for binding in result.bindings:
            original = self.membership.originals[binding.ref.node_id]
            self.assertEqual(binding.birth.identity, original.identity)
            self.assertEqual(binding.source.identity,
                self.reuse.identity if reused and binding.ref.node_id == "api" else original.identity)
        self.assertEqual(len(snapshots), 1)
        within(self, snapshots[0], state["snapshot"])
        self.assertLessEqual(snapshots[0].accounted_bytes, 3 * 1024 * 1024)
        within(self, cold["accounting"].used, state["future"].settled)
        for entry in cold["queries"]:
            within(self, Footprint(*entry["peak"]), state["future"].peak)
        reconciles(self, cold["accounting"].used, cold)
        after = self.proof_snapshot()
        self.assertTrue(self.advance(command).replayed)
        self.assertEqual(self.proof_snapshot(), after)

    def test_transferred_carry_publication_and_cold_reader_fit(self):
        self.measure_fit(reused=False)

    def test_new_source_transferred_birth_publication_and_cold_reader_fit(self):
        self.measure_fit(reused=True)

    def test_bound_transfer_dependency_loss_refuses_without_cold_fallback(self):
        command, visited = self.carry_command(), []
        original, query = ConfigurationAcceptanceStore._preflight, _EvidenceRead.query
        before = self.transfer_snapshot()
        def preflight(store, prepared):
            result = original(store, prepared)
            read = prepared.evidence_read
            dependencies = getattr(prepared.read_bounds, "transfer_dependencies", ())
            self.assertTrue(dependencies, "bound transfer lacks exact immutable dependency closure")
            identity = self.original.identity
            expected_sources = {
                ("cpk_effect_attempt_intents", identity),
                ("cpk_effect_attempt_outcomes", identity),
                ("cpk_activity_events", self.completion.original_event_id),
                ("cpk_activity_events", self.completion.direct_event_id),
                ("configuration-receipt-context", "workspace-a", self.revision),
                ("configuration-source-plan", "workspace-a", self.original.intent.source.plan_id),
            }
            expected = {}
            for ref in self.refs:
                digest = sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest()
                key = ("configuration-accepted-transfer", *self.key(ref), ref.workspace_id,
                    ref.allocation_id, digest, self.revision)
                expected[key] = ({(identity, ref.artifact_id)}, expected_sources)
            self.assertEqual(len(dependencies), len(expected))
            self.assertEqual({key: (set(refs), set(sources)) for key, refs, sources in dependencies}, expected)
            def prove():
                return store._accepted_transfer(read, self.key(self.refs[0]), self.refs[0], self.revision)
            # Select this root's actual closure, independent of tuple ordering.
            dependencies = {entry[0]: entry[1:] for entry in dependencies}
            memo = next(key for key in dependencies if key[1:5] == self.key(self.refs[0]))
            refs, sources = expected[memo]
            entries = [(read.sources, memo)] + [(read.refs, key) for key in refs] + [(read.sources, key) for key in sources]
            for cache, key in entries:
                saved, sql = cache.pop(key), []
                def observed_query(reader, statement, params=(), **kwargs):
                    sql.append(statement)
                    return query(reader, statement, params, **kwargs)
                try:
                    with mock.patch.object(_EvidenceRead, "query", observed_query), self.assertRaises(_Unavailable):
                        prove()
                    self.assertTrue(all(query_role(statement) in ("pair", "transfer-anchor", "cleanup-anchor")
                        for statement in sql), "missing dependency triggered unreserved cold proof SQL")
                    self.assertNotIn(key, cache)
                finally:
                    cache[key] = saved
                visited.append(key)
            copied = _EvidenceRead(read.connection)
            copied.refs.update(read.refs)
            copied.sources.update(read.sources)
            sql = []
            with mock.patch.object(_EvidenceRead, "query", observed_query), self.assertRaises(_Unavailable):
                store._accepted_transfer(copied, self.key(self.refs[0]), self.refs[0], self.revision)
            self.assertTrue(all(query_role(statement) in ("pair", "transfer-anchor", "cleanup-anchor")
                for statement in sql), "copied reader triggered unreserved cold proof SQL")
            prove()
            return result
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.advance(command)
        self.assertEqual(len(visited), 8)
        self.assertEqual(self.transfer_snapshot(), before)
