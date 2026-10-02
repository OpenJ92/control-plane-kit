"""#1923 reader integrity and capacity; no B2 reuse or provider authority."""
from dataclasses import replace
from hashlib import sha256
import unittest

import psycopg
import rfc8785

from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt
from tests.configuration_evidence_history_fixture import ConfigurationEvidenceHistoryFixture


class _ObservedRows:
    """Measure real returned PostgreSQL rows; never replace reader results."""
    def __init__(self, cursor, observations, query=""):
        self.cursor, self.observations = cursor, observations
        self.position = 0
        self.query = str(query)

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, *args, **kwargs):
        self.observations["bytes"] += 256
        self.observations["statements"] = self.observations.get("statements", 0) + 1
        self.query = str(args[0] if args else kwargs["query"])
        self.cursor.execute(*args, **kwargs)
        self.position = 0
        return self

    def executemany(self, *args, **kwargs):
        raise AssertionError("compact-reader telemetry does not admit unobserved executemany")

    def copy(self, *args, **kwargs):
        raise AssertionError("compact-reader telemetry does not admit unobserved COPY")

    def stream(self, *args, **kwargs):
        raise AssertionError("compact-reader telemetry does not admit unobserved streaming")

    def nextset(self):
        self.position = 0
        return self.cursor.nextset()

    def _record(self, row):
        if row is not None:
            self.observations["rows"] += 1
            self.observations["bytes"] += 128
            result = self.cursor.pgresult
            if result is None:
                raise AssertionError("PostgreSQL transport result was not observed")
            for column, value in enumerate(row):
                if result.ftype(column) == 17 and value is not None:
                    # bytea uses native octets, not its hex wire representation.
                    size = len(value)
                else:
                    if result.fformat(column) != 0:
                        raise AssertionError("unobserved binary text-cast metric")
                    raw = result.get_value(self.position, column)
                    # Text-format SQL results retain PostgreSQL's ::text bytes,
                    # including JSONB spaces. Never re-canonicalize decoded JSON.
                    size = 0 if raw is None else len(raw)
                self.observations["largest_cell"] = max(self.observations["largest_cell"], size)
                self.observations["bytes"] += size + 16
            self.position += 1
            if "cpk_effect_attempt_intents" in self.query:
                def has_value(value, expected):
                    if isinstance(value, dict):
                        return any(has_value(item, expected) for item in value.values())
                    if isinstance(value, (tuple, list)):
                        return any(has_value(item, expected) for item in value)
                    return isinstance(value, str) and value == expected
                for event_id in self.observations.get("source_reads", {}):
                    if has_value(row, event_id):
                        self.observations["source_reads"][event_id] += 1
        return row

    def fetchone(self):
        return self._record(self.cursor.fetchone())

    def fetchall(self):
        return [self._record(row) for row in self.cursor.fetchall()]

    def fetchmany(self, *args):
        return [self._record(row) for row in self.cursor.fetchmany(*args)]

    def __iter__(self):
        for row in self.cursor:
            yield self._record(row)


class _ObservedConnection:
    def __init__(self, connection, observations):
        self.connection, self.observations = connection, observations

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args, **kwargs):
        self.observations["bytes"] += 256
        self.observations["statements"] = self.observations.get("statements", 0) + 1
        query = args[0] if args else kwargs["query"]
        return _ObservedRows(self.connection.execute(*args, **kwargs), self.observations, query)

    def cursor(self, *args, **kwargs):
        return _ObservedRows(self.connection.cursor(*args, **kwargs), self.observations)

    def executemany(self, *args, **kwargs):
        raise AssertionError("compact-reader telemetry does not admit unobserved executemany")

    def copy(self, *args, **kwargs):
        raise AssertionError("compact-reader telemetry does not admit unobserved COPY")


class PostgresConfigurationEvidenceTests(ConfigurationEvidenceHistoryFixture, unittest.TestCase):
    def test_public_and_private_attempt_writers_require_owner_preparation(self):
        for method in ("insert_absent", "_insert_absent"):
            with self.subTest(method=method):
                self.reset_start_truth()
                candidate, _ = self.seed_recorded_source(include_attempt=False)
                before = self.complete_start_snapshot()
                with self.assertRaises(OperationsRecordError):
                    with self.unit_of_work() as uow:
                        getattr(uow.stores.effect_attempts, method)(candidate)
                        uow.commit()
                self.assertEqual(self.complete_start_snapshot(), before)

    def test_compact_source_projects_exact_original_ref_source_and_event(self):
        with self.unit_of_work() as uow:
            self.source_reader(uow.stores)
        attempt, intent = self.seed_recorded_source()
        ref = intent.configuration_instances.instances[0]
        before = self.complete_start_snapshot()
        result = self.read_source(attempt, ref)
        self.assertEqual(result.state, "complete")
        self.assertEqual(result.source.identity, attempt.state.identity)
        self.assertEqual(result.source.request_fingerprint, attempt.state.request_fingerprint)
        self.assertEqual(result.source.original_event_id, attempt.original_start_event.event_id)
        self.assertEqual(result.source.original_event_ordinal, attempt.original_start_event.ordinal)
        self.assertEqual(result.source.source, intent.source)
        self.assertEqual(result.source.kind, intent.kind)
        self.assertEqual(result.source.operation, intent.operation)
        self.assertEqual(result.source.ref, ref)
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_compact_source_rejects_wrong_hash_domain_scope_or_closed_selection(self):
        with self.unit_of_work() as uow:
            self.source_reader(uow.stores)
        for corruption in ("hash", "domain", "scope", "duplicate", "missing-selected-ref",
                           "foreign-other-ref", "unknown-selection-field"):
            with self.subTest(corruption=corruption):
                self.reset_start_truth()
                intent = self.intent()
                ref = intent.configuration_instances.instances[0]
                descriptor = intent.descriptor()
                if corruption == "scope":
                    descriptor["source"]["workspace_id"] = "foreign-workspace"
                if corruption == "duplicate":
                    selection = descriptor["configuration_instances"]
                    selection["instances"].append(selection["instances"][0])
                if corruption == "missing-selected-ref":
                    descriptor["configuration_instances"]["instances"].pop(0)
                if corruption == "foreign-other-ref":
                    descriptor["configuration_instances"]["instances"][1]["node_id"] = "foreign-node"
                if corruption == "unknown-selection-field":
                    descriptor["configuration_instances"]["ignored"] = "must-not-be-dropped"
                document = rfc8785.dumps(descriptor)
                domain = b"incorrect-source-domain\x00" if corruption == "domain" else (
                    b"control-plane-kit.runtime-effect-intent.v1\x00")
                fingerprint = "f" * 64 if corruption == "hash" else sha256(domain + document).hexdigest()
                attempt, _ = self.seed_recorded_source(intent=intent, preimage=document, fingerprint=fingerprint)
                before = self.complete_start_snapshot()
                evidence = self.read_source(attempt, ref)
                self.assertEqual(evidence.state, "unavailable")
                self.assertIsNone(evidence.source)
                self.assertEqual(self.complete_start_snapshot(), before)

    def test_exact_ref_selector_cannot_choose_a_different_allocation_or_material(self):
        with self.unit_of_work() as uow:
            self.source_reader(uow.stores)
        attempt, intent = self.seed_recorded_source()
        ref = intent.configuration_instances.instances[0]
        for changed in (replace(ref, allocation_id="other-allocation"),
                        replace(ref, content_digest="0" * 64), replace(ref, node_id="other-node")):
            with self.subTest(ref=changed):
                evidence = self.read_source(attempt, changed)
                self.assertEqual(evidence.state, "unavailable")
                self.assertIsNone(evidence.source)

    def test_large_valid_original_is_verified_without_transporting_its_product_contents(self):
        with self.unit_of_work() as uow:
            self.source_reader(uow.stores)
        self.configuration_selected_value = "x" * 200_000
        self.reset_start_truth()
        attempt, intent = self.seed_recorded_source()
        self.assertGreater(len(rfc8785.dumps(intent.descriptor())), 400_000)
        observed = {"rows": 0, "bytes": 0, "largest_cell": 0}
        ref = intent.configuration_instances.instances[0]
        with PostgresUnitOfWork(lambda: _ObservedConnection(
                psycopg.connect(self.database_url), observed)) as uow:
            result = self.source_reader(uow.stores)(attempt.state.identity, ref)
        self.assertEqual(result.state, "complete")
        self.assertEqual(result.source.ref, ref)
        self.assertGreater(observed["rows"], 0)
        self.assertLessEqual(observed["largest_cell"], 65_536)
        self.assertLessEqual(observed["rows"], 4096)
        # E3's entire per-claim source/context envelope, not just its largest
        # cell. Splitting the 400KB original across cells cannot satisfy this.
        self.assertLessEqual(observed["bytes"], 80 * 1024)

    def test_64_claims_returns_the_complete_ordered_identity_set_and_direct_birth(self):
        ref, attempts = self.seed_recorded_claims(64)
        before = self.complete_start_snapshot()
        evidence = self.read_allocation(ref)
        self.assertEqual(evidence.state, "complete")
        root = attempts[0].state.identity
        self.assertEqual(evidence.birth.identity, root)
        self.assertEqual(evidence.birth.birth_identity, root)
        self.assertEqual(evidence.birth.birth_artifact_id, ref.artifact_id)
        expected = sorted((attempt.state.identity for attempt in attempts),
            key=lambda identity: (identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id))
        self.assertEqual([claim.identity for claim in evidence.claims], expected)
        for claim in (evidence.birth, *evidence.claims):
            self.assertEqual(claim.ref, ref)
            self.assertEqual(claim.birth_identity, root)
            self.assertEqual(claim.birth_artifact_id, ref.artifact_id)
            self.assertEqual(claim.source.identity, claim.identity)
            self.assertEqual(claim.source.ref, ref)
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_65th_claim_returns_capacity_without_a_truncated_or_empty_complete_set(self):
        ref, _ = self.seed_recorded_claims(65)
        before = self.complete_start_snapshot()
        self.assert_allocation_unavailable(self.read_allocation(ref), state="capacity")
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_exact_original_replay_at_64_claims_does_not_admit_a_65th_claim(self):
        _ref, attempts = self.seed_recorded_claims(64)
        # Observation of explicitly recorded history does not claim B1 admitted
        # the other historical uses or can admit a new same-allocation use.
        command = self.configuration_command()
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("capacity-boundary replay sampled time"):
            result = service.execute(command)
        self.assertEqual(result, ExistingAttempt(attempts[0]))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_shared_birth_source_is_transported_once_and_absence_queries_are_not_free(self):
        ref, attempts = self.seed_recorded_claims(2)
        observed = {"rows": 0, "bytes": 0, "largest_cell": 0,
            "source_reads": {attempt.original_start_event.event_id: 0 for attempt in attempts}}
        with PostgresUnitOfWork(lambda: _ObservedConnection(
                psycopg.connect(self.database_url), observed)) as uow:
            evidence = self.allocation_reader(uow.stores)(ref)
        self.assertEqual(evidence.state, "complete")
        # Root and its claim share one original source. Bounded size/marker
        # queries remain separately charged; each full source proof arrives once.
        self.assertEqual(set(observed["source_reads"].values()), {1})
        self.assertGreater(observed["rows"], 0)
        self.assertLessEqual(observed["bytes"], 16 * 1024 * 1024)
        missing = replace(ref, allocation_id="absent-allocation")
        observed = {"rows": 0, "bytes": 0, "largest_cell": 0}
        with PostgresUnitOfWork(lambda: _ObservedConnection(
                psycopg.connect(self.database_url), observed)) as uow:
            self.assert_allocation_unavailable(self.allocation_reader(uow.stores)(missing))
        self.assertGreaterEqual(observed.get("statements", 0), 1)
        self.assertGreaterEqual(observed["bytes"], 256)

    def test_corrupt_original_source_under_a_claim_is_unavailable_not_non_use(self):
        ref, attempts = self.seed_recorded_claims(2)
        self.connection.execute(
            "UPDATE cpk_effect_attempt_intents SET preimage=preimage || ' '::bytea "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
            ("run-a", attempts[1].state.identity.activity_id, 1))
        self.assert_allocation_unavailable(self.read_allocation(ref))

    def test_false_root_chain_or_cycle_is_unavailable_without_recursive_resolution(self):
        ref, attempts = self.seed_recorded_claims(3)
        first, second = (attempt.state.identity.activity_id for attempt in attempts[1:])
        # Nonbirth use points at another nonbirth use. All FK keys still exist.
        self.connection.execute(
            "UPDATE cpk_effect_configuration_refs SET birth_activity_id=%s "
            "WHERE run_id='run-a' AND activity_id=%s AND artifact_id=%s", (second, first, ref.artifact_id))
        self.assert_allocation_unavailable(self.read_allocation(ref))
        self.connection.execute(
            "UPDATE cpk_effect_configuration_refs SET birth_activity_id=%s "
            "WHERE run_id='run-a' AND activity_id=%s AND artifact_id=%s", (first, second, ref.artifact_id))
        self.assert_allocation_unavailable(self.read_allocation(ref))

    def test_wrong_ref_digest_or_indexed_scope_is_unavailable(self):
        for column, value in (("ref_digest", "0" * 64), ("node_id", "wrong-node")):
            with self.subTest(column=column):
                ref, attempts = self.seed_recorded_claims(2)
                self.connection.execute(
                    f"UPDATE cpk_effect_configuration_refs SET {column}=%s "
                    "WHERE run_id='run-a' AND activity_id=%s AND artifact_id=%s",
                    (value, attempts[1].state.identity.activity_id, ref.artifact_id))
                self.assert_allocation_unavailable(self.read_allocation(ref))

    def test_missing_reciprocal_claim_is_unavailable_inside_the_uncommitted_transaction(self):
        ref, attempts = self.seed_recorded_claims(2)
        before = self.complete_start_snapshot()
        with self.unit_of_work() as uow:
            uow.stores.connection.execute("SET CONSTRAINTS ALL DEFERRED")
            uow.stores.connection.execute(
                "DELETE FROM cpk_configuration_claims WHERE run_id='run-a' AND activity_id=%s "
                "AND attempt=1 AND artifact_id=%s", (attempts[1].state.identity.activity_id, ref.artifact_id))
            evidence = self.allocation_reader(uow.stores)(ref)
            self.assert_allocation_unavailable(evidence)
            # Deliberate corruption is rolled back, never committed as history.
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_current_validator_refuses_structurally_valid_wrong_birth_without_repair(self):
        ref, attempts = self.seed_recorded_claims(3)
        first, second = (attempt.state.identity.activity_id for attempt in attempts[1:])
        before = self.complete_start_snapshot()

        class RollBackCorruption(Exception):
            pass

        with self.assertRaises(RollBackCorruption):
            with self.connection.transaction():
                # Existing nonbirth key satisfies its FK and is_birth CHECK;
                # only the owner can reject its non-root provenance.
                self.connection.execute(
                    "UPDATE cpk_effect_configuration_refs SET birth_activity_id=%s "
                    "WHERE run_id='run-a' AND activity_id=%s AND artifact_id=%s",
                    (second, first, ref.artifact_id))
                with self.assertRaises(SchemaInstallationError):
                    install_schema(self.connection)
                self.assertEqual(self.connection.execute(
                    "SELECT birth_activity_id FROM cpk_effect_configuration_refs "
                    "WHERE run_id='run-a' AND activity_id=%s AND artifact_id=%s",
                    (first, ref.artifact_id)).fetchone(), (second,))
                raise RollBackCorruption()
        self.assertEqual(self.complete_start_snapshot(), before)
