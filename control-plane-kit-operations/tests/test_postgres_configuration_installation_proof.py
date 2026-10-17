"""Compact installation proof over recorded PostgreSQL history.

Raw insertion here is a read-boundary corruption fixture, never successful
start/fold admission or provider proof. Core owns receipt semantics; these tests
prove retained origin, transport, cache identity and transaction accounting.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import unittest
from unittest import mock

import psycopg
import rfc8785

from control_plane_kit_core.configuration_invocation import (
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import ActivityEventKind, fold_effect_attempt
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_core.secret_installation import secret_installation_receipt_for_result
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery, SecretReference, SecretReferenceEnvironmentDelivery, SecretUseIntent,
)
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as Footprint
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord, ExecutionEffectOutcome, effect_outcome_transition, effect_outcome_failure,
    _require_correlated_outcome,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import (
    _Capacity, _EvidenceRead, _Unavailable, _composed_read,
)
from control_plane_kit_operations.postgres.configuration_source import read_source
from control_plane_kit_operations.postgres import effect_attempt_store, effect_outcome_store
from tests.configuration_cleanup_history_fixture import event_for, insert
from tests.configuration_evidence_history_fixture import ConfigurationEvidenceHistoryFixture
from tests.test_postgres_secret_installation_admission import ProofReadObserver
from tests.test_secret_installation_admission import receipt_result


DOMAIN = b"control-plane-kit.runtime-effect-intent.v1\x00"
PROFILES = ("secret_resolution_receipt", "secret_installation_receipt",
            "configuration_installation_receipt")


def proof_sql(statement):
    text = str(statement)
    return "cpk_effect_attempt_intents" in text and (
        "secret_deliveries" in text or "secret_selections" in text)


def difference(after, before):
    return Footprint(*(getattr(after, name) - getattr(before, name)
        for name in ("records", "value_octets", "scalar_markers", "statements")))


class PostgresConfigurationInstallationProofTests(ConfigurationEvidenceHistoryFixture, unittest.TestCase):
    def test_probe_decoder_is_closed_and_accepts_real_postgres_material_and_empty_rows(self):
        name = "control_plane_kit_operations.postgres.configuration_installation_proof"
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            self.fail("missing original-installation proof owner")
        decode = getattr(module, "_decode_probe", None)
        self.assertTrue(callable(decode), "missing closed probe scalar decoder")
        # Proposed pure contract: exact five-cell tuple -> (empty, four widths).
        # It refuses status 0, non-int/bool cells, nonpositive target lengths,
        # non-array minimum widths, int32 overflow and false exact-empty claims.
        controls = []
        for mode in ("selected", "empty"):
            intent, attempt, outcome = self.recorded_terminal(mode=mode)
            with self.reading(intent, attempt) as (uow, read, source, observer, rows):
                self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
                probe = next(row for sql, row in rows if proof_sql(sql))
                prior, statements = read.used, len(observer.queries)
                self.assertEqual(decode(probe), (mode == "empty", tuple(probe[1:])))
                self.assertEqual((read.used, len(observer.queries)), (prior, statements))
                controls.append(probe)
        material, empty = controls
        self.assertEqual((material[0], empty[0]), (1, 2))
        cases = [None, (), list(material), material[:-1], (*material, 0),
            (0, 0, 0, 0, 0), (3, *material[1:]), (True, *material[1:]),
            (2, *material[1:]), (1, 0, *material[2:]), (1, material[1], 0, *material[3:]),
            (1, *material[1:3], 1, material[4]), (1, *material[1:4], 1)]
        for position in range(1, 5):
            for invalid in (None, True, -1, 2**31, "private-canary", 1.5):
                row = list(material)
                row[position] = invalid
                cases.append(tuple(row))
        for index, row in enumerate(cases):
            with self.subTest(case=index):
                self.assert_unavailable(lambda: decode(row))

    def test_native_original_guards_refuse_real_malformed_and_hash_drift(self):
        # Corrupt the actual retained row only after source linkage is read.
        # No synthetic successful cursor result or replacement proof query.
        # All three are hash-drift controls, not native decoder coverage.
        cases = ("utf8", "json", "hash")
        for case in cases:
            with self.subTest(native=case):
                intent, attempt, outcome = self.recorded_terminal()
                before = self.installation_snapshot()
                with self.reading(intent, attempt) as (uow, read, source, observer, rows):
                    observer.enabled = False
                    locator = (source.identity.run_id.value, source.identity.activity_id, source.identity.attempt)
                    preimage = {"utf8": b"\xff", "json": b"{private-canary", "hash": b"{}"}[case]
                    observer.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s "
                        "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (preimage, *locator))
                    observer.enabled = True
                    self.assert_unavailable(lambda: self.terminal(uow, source, read))
                    material = [row for sql, row in rows if proof_sql(sql)]
                    self.assertTrue(material, "refusal never reached the actual original proof query")
                    self.assertTrue(all(len(row) == 5 and row[0] == 0 for row in material))
                    self.assertFalse(any(type(key) is tuple and key[0] == "configuration-installation-proof"
                        for key in read.sources))
                    self.assertLessEqual(observer.largest_cell, 80 * 1024)
                    self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)
                    self.assertEqual(sum(sql.startswith("SAVEPOINT ") for sql in observer.queries),
                        sum(sql.startswith("RELEASE SAVEPOINT ") for sql in observer.queries))
                    observer.enabled = False
                    self.assertEqual(observer.connection.execute("SELECT 1").fetchone(), (1,))
                self.assertEqual(self.installation_snapshot(), before)

    def test_native_size_and_missing_row_controls_preserve_constraints_and_use_actual_proof_sql(self):
        intent, attempt, outcome = self.recorded_terminal()
        locator = (attempt.state.identity.run_id.value, attempt.state.identity.activity_id, attempt.state.identity.attempt)
        before = self.installation_snapshot()
        # The current schema makes over-native and empty originals impossible.
        # Exercise those guards without disabling CHECK/FK constraints.
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            for invalid in (b"", b"x" * (1024 * 1024 + 1)):
                connection.execute("SAVEPOINT test_native_size")
                with self.assertRaises(psycopg.errors.CheckViolation):
                    connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s "
                        "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (invalid, *locator))
                connection.execute("ROLLBACK TO SAVEPOINT test_native_size")
                connection.execute("RELEASE SAVEPOINT test_native_size")
        captured, query = [], _EvidenceRead.query
        def record_query(read, statement, params=(), **bounds):
            if proof_sql(statement):
                captured.append((statement, params))
            return query(read, statement, params, **bounds)
        with self.reading(intent, attempt) as (uow, read, source, observer, rows):
            with mock.patch.object(_EvidenceRead, "query", record_query):
                self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            self.assertEqual(len(captured), 2)
            observer.enabled = False
            for statement, params in captured:
                # Query control only: the owner's unchanged SQL is executed
                # against an absent exact run key, not counterfeit cursor rows.
                self.assertIn(locator[0], params)
                absent = tuple("missing-installation-run" if value == locator[0] else value for value in params)
                result = observer.connection.execute(statement, absent).fetchall()
                self.assertTrue(not result or all(len(row) == 5 and row[0] == 0 for row in result))
                self.assertFalse(any(type(cell) is str for row in result for cell in row))
        self.assertEqual(self.installation_snapshot(), before)

    def test_matching_native_commitments_reach_real_decoder_and_bounded_owner_closure(self):
        name = "control_plane_kit_operations.postgres.configuration_installation_proof"
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            self.fail("missing original-installation proof owner")
        prove = getattr(module, "read_installation_proof", None)
        self.assertTrue(callable(prove), "missing bounded original-installation reader")
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            projections = read.sources[("cpk_effect_attempt_intents", source.identity)]
            self.assertEqual(len([row for sql, row in rows if proof_sql(sql)]), 2)
            original_probe = next(sql for sql, row in rows if proof_sql(sql))
        for invalid, sqlstate in ((b"\xff", "22021"), (b"{private-canary", "22P02")):
            with self.subTest(sqlstate=sqlstate):
                self.reset_start_truth()
                fingerprint = sha256(DOMAIN + invalid).hexdigest()
                recorded, _ = self.seed_recorded_source(intent=intent,
                    preimage=invalid, fingerprint=fingerprint)
                # Explicit query-control history, NOT admitted original truth.
                # Raw hash, native size, attempt and original event are real and
                # consistent. Full read_source would reject the malformed bytes;
                # retain the valid context only to exercise this narrower owner.
                context = replace(source, request_fingerprint=fingerprint)
                self.assertEqual(recorded.state.identity, context.identity)
                self.assertEqual((recorded.original_start_event.event_id, recorded.original_start_event.ordinal),
                    (context.original_event_id, context.original_event_ordinal))
                before = self.installation_snapshot()
                errors, query = [], _EvidenceRead.query
                def native_query(read, statement, params=(), **bounds):
                    prior = read.used
                    try:
                        return query(read, statement, params, **bounds)
                    except psycopg.DataError as error:
                        self.assertEqual(statement, original_probe, "native control replaced the production query")
                        self.assertTrue(all(value in params for value in (
                            context.identity.run_id.value, context.identity.activity_id, context.identity.attempt,
                            fingerprint, context.original_event_id, context.original_event_ordinal)))
                        errors.append((error.sqlstate, prior, bounds))
                        raise
                observer = ProofReadObserver(psycopg.connect(self.database_url))
                with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
                    # Real pre-decode guards: no SQL rewrite, fake row or disabled
                    # constraint. The query hashes the actual stored bytes.
                    self.assertEqual(observer.connection.execute(
                        "SELECT octet_length(i.preimage) BETWEEN 1 AND 1048576, "
                        "encode(sha256(%s::bytea || i.preimage),'hex')=i.request_fingerprint, "
                        "i.request_fingerprint=%s, a.request_fingerprint=i.request_fingerprint, "
                        "(a.original_event_id,a.original_event_run_id,a.original_event_ordinal)="
                        "(i.original_event_id,i.original_event_run_id,i.original_event_ordinal) "
                        "FROM cpk_effect_attempt_intents i JOIN cpk_effect_attempts a "
                        "USING (run_id,activity_id,attempt) WHERE (i.run_id,i.activity_id,i.attempt)=(%s,%s,%s)",
                        (DOMAIN, fingerprint, context.identity.run_id.value, context.identity.activity_id,
                            context.identity.attempt)).fetchone(), (True,) * 5)
                    read.sources[("cpk_effect_attempt_intents", context.identity)] = tuple(
                        replace(value, request_fingerprint=fingerprint) for value in projections)
                    read.sources[("cpk_activity_events", context.original_event_id)] = recorded.original_start_event
                    observer.enabled = True
                    with mock.patch.object(_EvidenceRead, "query", native_query):
                        self.assert_unavailable(lambda: prove(read, context))
                    self.assertEqual(len(errors), 1, "native query did not reach its real parse boundary")
                    code, prior, bounds = errors[0]
                    self.assertEqual(code, sqlstate)
                    reservation = Footprint(bounds["records"] * bounds.get("identities", 1), bounds["octets"],
                        bounds["records"] * bounds["cells"], 1)
                    self.assertEqual(read.used, prior.plus(reservation).plus(Footprint(0, 0, 0, 2)))
                    self.assertEqual(sum(sql.startswith("ROLLBACK TO SAVEPOINT ") for sql in observer.queries), 1)
                    self.assertEqual(sum(sql.startswith("RELEASE SAVEPOINT ") for sql in observer.queries), 1)
                    self.assertFalse(any(type(key) is tuple and key[0] == "configuration-installation-proof"
                        for key in read.sources))
                    self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)
                    observer.enabled = False
                    self.assertEqual(observer.connection.execute("SELECT 1").fetchone(), (1,))
                self.assertEqual(self.installation_snapshot(), before)

    def material_intent(self, mode):
        original = self.intent()
        material, = original.products
        reference = SecretReference("secret://local/installation/token")
        if mode == "selected":
            deliveries = (SecretEnvironmentDelivery("CONTROL_TOKEN", reference,
                SecretUseIntent.APPLICATION_CONTROL_TOKEN), SecretEnvironmentDelivery("AUDIT_TOKEN",
                SecretReference("secret://local/installation/audit"), SecretUseIntent.APPLICATION_CONTROL_TOKEN))
            selections = tuple(SecretVersionSelection(value.reference, value.intent, "version-a")
                for value in deliveries)
        elif mode == "reference":
            deliveries = (SecretReferenceEnvironmentDelivery("CONTROL_REFERENCE", reference),)
            selections = ()
        else:
            self.assertEqual(mode, "empty")
            deliveries, selections = (), ()
        product = replace(material.product, runtime_contract=replace(
            material.product.runtime_contract, secret_deliveries=deliveries))
        reference = ProductReference.from_document(ProductDescriptorCodec().encode_document(product))
        material = replace(material, product=product, reference=reference, secret_selections=selections)
        return replace(original, products=(material,))

    def recorded_terminal(self, *, mode="selected", change_document=None, change_result=None):
        self.reset_start_truth()
        intent = self.material_intent(mode)
        document = intent.descriptor()
        if change_document is not None:
            change_document(document)
        preimage = rfc8785.dumps(document)
        fingerprint = sha256(DOMAIN + preimage).hexdigest()
        attempt, _ = self.seed_recorded_source(intent=intent, preimage=preimage, fingerprint=fingerprint)
        request = runtime_effect_request_for_intent(intent, effect_id=attempt.original_start_event.event_id)
        result = receipt_result(request) if mode == "selected" else RuntimeEffectResult.succeeded(request.effect_id)
        configuration = dict(profile="configuration-installation-receipt.v1",
            request_fingerprint=sha256(DOMAIN + rfc8785.dumps(intent.descriptor())).hexdigest(),
            selection_fingerprint=configuration_invocation_selection_fingerprint(intent.configuration_instances),
            disposition="created", prior_realization_fingerprint=None, current_realization_fingerprint="b" * 64)
        if mode == "selected":
            result = replace(result, evidence={**result.evidence, PROFILES[2]: configuration})
            self.assertIsNotNone(secret_installation_receipt_for_result(request, result))
        else:
            self.assertIsNone(secret_installation_receipt_for_result(request, result))
        # Recommit malformed material coherently: linkage alone must stay green
        # while the new material proof rejects it. No original is repaired.
        evidence = deepcopy(result.evidence)
        for profile in PROFILES:
            if profile in evidence:
                evidence[profile]["request_fingerprint"] = fingerprint
        result = replace(result, evidence=evidence)
        if change_result is not None:
            result = change_result(result)
        outcome = ExecutionEffectOutcome(attempt.state.identity, fingerprint, result)
        state = fold_effect_attempt(attempt.state, effect_outcome_transition(outcome), fence=attempt.state.fence)
        event = event_for(state, event_id="recorded-installation-terminal", ordinal=4,
            kind=ActivityEventKind.STEP_SUCCEEDED if result.kind.value == "succeeded" else ActivityEventKind.STEP_FAILED,
            failure=effect_outcome_failure(outcome))
        event = replace(event, occurred_at="2030-01-01T00:00:01Z")
        retained = EffectAttemptRecord(state, attempt.original_start_event, event)
        record = EffectAttemptOutcomeRecord(intent.source.workspace_id, outcome, retained, ())
        with self.unit_of_work() as uow:
            uow.stores.execution.add_event(event)
            insert(uow.stores.connection, "cpk_effect_attempt_outcomes", effect_outcome_store._COLUMN_NAMES,
                effect_outcome_store._record_values(record, intent.source.request_id))
            columns = effect_attempt_store._COLUMN_NAMES[3:]
            values = effect_attempt_store._record_values(retained)
            uow.stores.connection.execute("UPDATE cpk_effect_attempts SET " +
                ",".join(name + "=%s" for name in columns) +
                " WHERE run_id=%s AND activity_id=%s AND attempt=%s", (*values[3:], *values[:3]))
            uow.commit()
        return intent, retained, outcome

    def installation_snapshot(self):
        return self.complete_start_snapshot(), tuple(self.connection.execute(
            "SELECT * FROM cpk_effect_attempt_outcomes ORDER BY run_id,activity_id,attempt").fetchall()), tuple(
            self.connection.execute("SELECT * FROM cpk_effect_attempt_intents "
                "ORDER BY run_id,activity_id,attempt").fetchall())

    @contextmanager
    def reading(self, intent, attempt, *, after_row=None):
        rows = []
        def observe(observer, query, row):
            rows.append((str(query), row))
            if after_row is not None:
                after_row(observer, query, row)
        observer = ProofReadObserver(psycopg.connect(self.database_url), observe)
        with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
            source = read_source(uow.stores.connection, attempt.state.identity,
                intent.configuration_instances.instances[0], read=read)
            self.assertEqual(source.state, "complete", "recorded linkage premise must succeed independently")
            self.assertEqual(source.source.request_fingerprint, attempt.state.request_fingerprint)
            observer.enabled = True
            yield uow, read, source.source, observer, rows
            observer.enabled = False

    def terminal(self, uow, source, read):
        return uow.stores.effect_outcomes._configuration_terminal(source, read)

    def assert_unavailable(self, call):
        with self.assertRaises(_Unavailable) as caught:
            call()
        self.assertLessEqual(len(str(caught.exception).encode()), 256)
        self.assertNotIn("private-canary", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def proof_keys(self, read):
        keys = [key for key in read.sources if type(key) is tuple and key[0] == "configuration-installation-proof"]
        self.assertEqual(len(keys), 1, "missing exact-original compact installation proof")
        return keys

    def test_selected_success_reads_only_four_original_fragments_and_matches_actual_result(self):
        intent, attempt, outcome = self.recorded_terminal()
        before = self.installation_snapshot()
        with self.reading(intent, attempt) as (uow, read, source, observer, rows):
            prior = read.used
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            material = [row for query, row in rows if proof_sql(query)]
            self.assertEqual(len(material), 2, "cold selected material requires probe and guarded fetch")
            probe, fetch = material
            self.assertEqual(len(probe), 5)
            self.assertTrue(all(type(value) is int for value in probe))
            self.assertEqual(probe[0], 1)
            self.assertLessEqual(sum(len(str(value)) for value in probe), 41)
            self.assertEqual(len(fetch), 5)
            self.assertEqual(fetch[0], 1)
            self.assertTrue(all(type(value) is str for value in fetch[1:]))
            self.assertEqual(tuple(len(value.encode()) for value in fetch[1:]), tuple(probe[1:]))
            self.assertEqual(fetch[1:3], ("docker", "api"))
            self.assertNotIn("configuration_artifacts", "".join(fetch[1:]))
            # Each terminal/event/proof query here charges one identity per
            # returned row; source's three-identity projection precedes prior.
            self.assertEqual(difference(read.used, prior).records, observer.rows)
            self.assertEqual(difference(read.used, prior).value_octets, observer.value_bytes)
            self.assertEqual(difference(read.used, prior).scalar_markers, sum(len(row) for _, row in rows))
            self.assertEqual(difference(read.used, prior).statements, len(observer.queries))
            self.assertEqual(sum(query.startswith("SAVEPOINT ") for query in observer.queries), 1)
            self.assertEqual(sum(query.startswith("RELEASE SAVEPOINT ") for query in observer.queries), 1)
            self.assertEqual(self.proof_keys(read)[0], ("configuration-installation-proof", source.identity,
                source.request_fingerprint, source.original_event_id, source.original_event_ordinal))
        self.assertEqual(self.installation_snapshot(), before)

    def test_history_linkage_does_not_admit_missing_foreign_or_conflicting_installation(self):
        def changed(result, profile, case):
            evidence = deepcopy(result.evidence)
            if case == "missing":
                del evidence[profile]
            else:
                evidence[profile]["selection_fingerprint" if case == "foreign" else
                    "current_realization_fingerprint"] = "f" * 64
            return replace(result, evidence=evidence)
        cases = [(profile, case) for profile in PROFILES for case in ("missing", "foreign")]
        cases.append((PROFILES[2], "conflicting-realization"))
        for profile, case in cases:
            with self.subTest(profile=profile, case=case):
                intent, attempt, _ = self.recorded_terminal(change_result=lambda result: changed(result, profile, case))
                before = self.installation_snapshot()
                with self.reading(intent, attempt) as (uow, read, source, _, _rows):
                    self.assert_unavailable(lambda: self.terminal(uow, source, read))
                self.assertEqual(self.installation_snapshot(), before)

    def test_closed_original_fragments_refuse_despite_self_consistent_linkage(self):
        def corrupt(document, case):
            material = document["products"][0]
            contract = material["product"]["runtime_contract"]
            selections = material["secret_selections"]
            if case == "missing-deliveries":
                del contract["secret_deliveries"]
            elif case == "missing-selections":
                del material["secret_selections"]
            elif case == "unknown-delivery":
                contract["secret_deliveries"][0]["kind"] = "private-canary"
            elif case == "duplicate-selection":
                selections.append(deepcopy(selections[0]))
            elif case == "missing-selection":
                selections.clear()
            elif case == "extra-selection":
                selections.append({**selections[0], "reference_id": "secret://local/foreign/token"})
            elif case == "unresolved-selection":
                selections[0]["version_id"] = None
            elif case == "noncanonical-selection":
                selections.reverse()
            elif case == "multiple-products":
                document["products"].append(deepcopy(material))
            elif case == "missing-product":
                document["products"].clear()
            else:
                material[case] = "foreign-target"
        for case in ("missing-deliveries", "missing-selections", "unknown-delivery", "duplicate-selection",
                "missing-selection", "extra-selection", "unresolved-selection", "noncanonical-selection", "multiple-products",
                "missing-product", "runtime_id", "node_id"):
            with self.subTest(case=case):
                intent, attempt, _ = self.recorded_terminal(change_document=lambda value: corrupt(value, case))
                before = self.installation_snapshot()
                with self.reading(intent, attempt) as (uow, read, source, _, _rows):
                    self.assert_unavailable(lambda: self.terminal(uow, source, read))
                self.assertEqual(self.installation_snapshot(), before)

    def test_exact_empty_and_reference_only_material_have_distinct_charged_cold_paths(self):
        for mode in ("empty", "reference"):
            with self.subTest(mode=mode):
                intent, attempt, outcome = self.recorded_terminal(mode=mode)
                with self.reading(intent, attempt) as (uow, read, source, _, rows):
                    prior = read.used
                    self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
                    material = [row for query, row in rows if proof_sql(query)]
                    self.assertEqual(len(material), 1 if mode == "empty" else 2)
                    self.assertEqual(material[0][0], 2 if mode == "empty" else 1)
                    self.assertGreater(read.used.accounted_bytes, prior.accounted_bytes)
                    if mode == "reference":
                        self.assertIn("environment-reference", material[1][3])
                def foreign_claim(result):
                    return replace(result, evidence={PROFILES[0]: {
                        "profile": "secret-resolution-receipt.v1", "request_fingerprint": "f" * 64,
                        "selection_fingerprint": "e" * 64}})
                intent, attempt, _ = self.recorded_terminal(mode=mode, change_result=foreign_claim)
                with self.reading(intent, attempt) as (uow, read, source, _, _rows):
                    self.assert_unavailable(lambda: self.terminal(uow, source, read))

    def test_failed_direct_outcome_preserves_no_proof_but_rejects_each_partial_success_claim(self):
        for profile in (None, *PROFILES):
            with self.subTest(profile=profile):
                def failed(result):
                    failure = RuntimeEffectResult.failed(result.effect_id,
                        RuntimeEffectFailure("fixture.failed", "No installation was verified."))
                    return replace(failure, evidence={} if profile is None else {profile: result.evidence[profile]})
                intent, attempt, outcome = self.recorded_terminal(change_result=failed)
                with self.reading(intent, attempt) as (uow, read, source, _, _rows):
                    if profile is None:
                        self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
                    else:
                        self.assert_unavailable(lambda: self.terminal(uow, source, read))

    def test_warm_terminal_revalidates_actual_receipt_without_new_proof_transport(self):
        intent, attempt, outcome = self.recorded_terminal()
        before = self.installation_snapshot()
        with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            proof_key, = self.proof_keys(read)
            proof = read.sources[proof_key]
            prior, statements = read.used, len(observer.queries)
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            self.assertEqual((read.used, len(observer.queries)), (prior, statements))
            key = ("cpk_effect_attempt_outcomes", source.identity)
            row, original, retained = read.sources[key]
            missing = replace(original, result=replace(original.result, evidence={
                name: value for name, value in original.result.evidence.items() if name != PROFILES[1]}))
            state = replace(retained.state, outcome_fingerprint=missing.outcome_fingerprint)
            old_event = retained.latest_transition_event
            event = replace(event_for(state, event_id=old_event.event_id, ordinal=old_event.ordinal,
                kind=old_event.kind, failure=effect_outcome_failure(missing)), occurred_at=old_event.occurred_at)
            changed_attempt = EffectAttemptRecord(state, retained.original_start_event, event)
            record = EffectAttemptOutcomeRecord(source.source.workspace_id, missing, changed_attempt, ())
            changed_row = effect_outcome_store._record_values(record, source.source.request_id)
            self.assertEqual((changed_row[:6], changed_row[7:11], changed_row[12:]),
                (row[:6], row[7:11], row[12:]))
            # Actual row/event reconstruction and ordinary correlation remain
            # valid; only installation proof is missing from this success.
            decoded, decoded_attempt = effect_outcome_store._outcome_from_events(changed_row,
                effect_outcome_store._decode_preimage(changed_row[6], changed_row[5]),
                retained.original_start_event, event)
            self.assertEqual((decoded, decoded_attempt), (missing, changed_attempt))
            _require_correlated_outcome(source.source.workspace_id, decoded, decoded_attempt)
            read.sources[key] = (changed_row, decoded, decoded_attempt)
            read.sources[("cpk_activity_events", event.event_id)] = event
            self.assert_unavailable(lambda: self.terminal(uow, source, read))
            self.assertEqual((read.used, len(observer.queries)), (prior, statements))
            self.assertIs(read.sources[proof_key], proof)
        self.assertEqual(self.installation_snapshot(), before)

    def test_warm_proof_revalidates_context_and_raw_fragments_without_repair(self):
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            key, = self.proof_keys(read)
            proof = read.sources[key]
            # Proposed private memo contract: immutable correlation + four exact
            # transported fragments. Neither is a cached authorization verdict.
            context = getattr(proof, "correlation", None)
            fragments = getattr(proof, "fragments", None)
            self.assertIsNotNone(context, "proof memo must retain revalidatable original correlation")
            self.assertEqual(len(fragments), 4)
            statements = len(observer.queries)
            original = context.secret_selections
            object.__setattr__(context, "secret_selections", ())
            try:
                self.assert_unavailable(lambda: self.terminal(uow, source, read))
                self.assertEqual(context.secret_selections, (), "reader repaired corrupted cache")
            finally:
                object.__setattr__(context, "secret_selections", original)
            read.sources[key] = replace(proof, fragments=(*fragments[:3], "[]"))
            self.assert_unavailable(lambda: self.terminal(uow, source, read))
            self.assertEqual(len(observer.queries), statements)

    def test_foreign_source_cannot_borrow_warm_original_proof(self):
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            self.proof_keys(read)
            for changed in (replace(source, request_fingerprint="f" * 64),
                    replace(source, original_event_id="foreign-original"),
                    replace(source, original_event_ordinal=source.original_event_ordinal + 1),
                    replace(source, ref=replace(source.ref, allocation_id="foreign-allocation"))):
                with self.subTest(field=changed):
                    statements = len(observer.queries)
                    self.assert_unavailable(lambda: self.terminal(uow, changed, read))
                    self.assertEqual(len(observer.queries), statements)

    def test_independent_cold_reader_charges_again_on_the_same_ledger(self):
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            first = [row for query, row in rows if proof_sql(query)]
            self.assertEqual(len(first), 2)
            before = read.used
            second = _EvidenceRead(uow.stores.connection)
            self.assertIs(second.accounting, read.accounting)
            source = read_source(uow.stores.connection, attempt.state.identity,
                intent.configuration_instances.instances[0], read=second).source
            self.assertEqual(self.terminal(uow, source, second), (outcome, attempt))
            self.assertEqual([row for query, row in rows if proof_sql(query)], first * 2)
            self.assertGreater(read.used.accounted_bytes, before.accounted_bytes)
            self.assertEqual(read.used, second.used)

    def test_original_growth_and_equal_width_drift_after_probe_refuse_before_payload_transfer(self):
        # Isolated SQL width control, not a second admission path: capture an
        # unchanged successful production fetch on a lawful committed original.
        # Keep every guard/context parameter and vary only one expected width.
        intent, attempt, outcome = self.recorded_terminal()
        before = self.installation_snapshot()
        captured, query = [], _EvidenceRead.query
        def capture_fetch(read, statement, params=(), **bounds):
            result = query(read, statement, params, **bounds)
            if (proof_sql(statement) and len(result) == 1 and len(result[0]) == 5
                    and result[0][0] == 1 and all(type(value) is str for value in result[0][1:])):
                captured.append((statement, params, result[0]))
            return result
        with self.reading(intent, attempt) as (uow, read, source, observer, rows):
            with mock.patch.object(_EvidenceRead, "query", capture_fetch):
                self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            self.assertEqual(len(captured), 1)
            statement, params, expected = captured[0]
            probe = next(row for sql, row in rows if proof_sql(sql))
            self.assertEqual(tuple(params[-4:]), tuple(probe[1:]))
            # These direct query controls use existing wire telemetry, not a
            # fabricated ledger charge or an injected successful cursor row.
            prior_rows, prior_bytes = observer.rows, observer.value_bytes
            positive = observer.execute(statement, params).fetchall()
            self.assertTrue(positive == [expected], "matching widths must preserve the real fetch")
            self.assertEqual(observer.rows - prior_rows, 1)
            self.assertEqual(observer.value_bytes - prior_bytes, 1 + sum(probe[1:]))
            for index in range(4):
                with self.subTest(width=index):
                    narrowed = list(params[-4:])
                    self.assertGreater(narrowed[index], 1)
                    narrowed[index] -= 1
                    prior_rows, prior_bytes = observer.rows, observer.value_bytes
                    refused = observer.execute(statement, (*params[:-4], *narrowed)).fetchall()
                    self.assertTrue(refused == [(0, None, None, None, None)],
                        "the unchanged production fetch must refuse a single narrowed width")
                    self.assertEqual(observer.rows - prior_rows, 1)
                    self.assertEqual(observer.value_bytes - prior_bytes, 1)
            observer.enabled = False
            self.assertEqual(uow.stores.connection.execute("SELECT 1").fetchone(), (1,))
        self.assertEqual(self.installation_snapshot(), before)

        # Post-probe hash drift: the original fingerprint is an immediate FK
        # parent and remains unchanged. This exercises the repeated raw-byte
        # commitment guard, separately from the isolated width control above.
        for case in ("growth", "same-width"):
            with self.subTest(case=case):
                intent, attempt, _ = self.recorded_terminal()
                before, injected = self.installation_snapshot(), []
                def drift(observer, query, row):
                    if not injected and proof_sql(query) and all(type(value) is int for value in row):
                        document = intent.descriptor()
                        document["products"][0]["secret_selections"][0]["version_id"] = (
                            "x" * 200_000 if case == "growth" else "version-b")
                        changed = rfc8785.dumps(document)
                        if case == "same-width":
                            self.assertEqual(len(changed), len(rfc8785.dumps(intent.descriptor())))
                        observer.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s "
                            "WHERE run_id='run-a' AND activity_id='start-api' AND attempt=1", (changed,))
                        injected.append(True)
                with self.reading(intent, attempt, after_row=drift) as (uow, read, source, observer, rows):
                    self.assert_unavailable(lambda: self.terminal(uow, source, read))
                    self.assertEqual(injected, [True])
                    material = [row for query, row in rows if proof_sql(query)]
                    self.assertEqual(len(material), 2)
                    self.assertEqual(material[1][0], 0)
                    self.assertTrue(all(value is None for value in material[1][1:]))
                    self.assertLess(observer.largest_cell, 80 * 1024)
                    observer.enabled = False
                    self.assertEqual(uow.stores.connection.execute("SELECT 1").fetchone(), (1,))
                self.assertEqual(self.installation_snapshot(), before)

    def test_database_decode_failure_keeps_reservation_and_charged_rollback_release(self):
        for invalid_sql in ("SELECT convert_from(decode('ff','hex'),'UTF8')",
                            "SELECT '{private-canary'::jsonb"):
            with self.subTest(sql_kind="utf8" if "convert_from" in invalid_sql else "json"):
                intent, attempt, _ = self.recorded_terminal()
                before, injected = self.installation_snapshot(), []
                actual = _EvidenceRead.query
                def fail_fetch(read, statement, params, **bounds):
                    # Inject a real PostgreSQL DataError at the reserved fetch.
                    # Same owner, connection, reservation and error handling;
                    # no mocked rows or fabricated successful proof.
                    if proof_sql(statement) and injected == ["probe"]:
                        injected.append((read.used, bounds))
                        return actual(read, invalid_sql, (), **bounds)
                    result = actual(read, statement, params, **bounds)
                    if proof_sql(statement):
                        injected.append("probe")
                    return result
                with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
                    with mock.patch.object(_EvidenceRead, "query", fail_fetch):
                        self.assert_unavailable(lambda: self.terminal(uow, source, read))
                    self.assertEqual(len(injected), 2)
                    prior, bounds = injected[1]
                    reservation = Footprint(bounds["records"] * bounds.get("identities", 1),
                        bounds["octets"], bounds["records"] * bounds["cells"], 1)
                    self.assertEqual(read.used, prior.plus(reservation).plus(Footprint(0, 0, 0, 2)))
                    self.assertTrue(any(query.startswith("ROLLBACK TO SAVEPOINT ") for query in observer.queries))
                    self.assertTrue(any(query.startswith("RELEASE SAVEPOINT ") for query in observer.queries))
                    self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)
                    observer.enabled = False
                    self.assertEqual(uow.stores.connection.execute("SELECT 1").fetchone(), (1,))
                self.assertEqual(self.installation_snapshot(), before)

    def test_capacity_after_probe_reserves_closure_without_fetch_or_refund(self):
        intent, attempt, _ = self.recorded_terminal()
        injected, holder = [], {}
        def exhaust(observer, query, row):
            if not injected and proof_sql(query) and all(type(value) is int for value in row):
                read = holder["read"]
                # Synthetic prior command usage, not fabricated returned data.
                additional = 16 * 1024 * 1024 - 768 - read.used.accounted_bytes
                self.assertGreater(additional, 0)
                read.used = read.used.plus(Footprint(0, additional, 0, 0))
                injected.append(read.used)
        before = self.installation_snapshot()
        with self.reading(intent, attempt, after_row=exhaust) as (uow, read, source, observer, rows):
            holder["read"] = read
            with self.assertRaises(_Capacity):
                self.terminal(uow, source, read)
            self.assertEqual(len(injected), 1)
            self.assertEqual(len([row for query, row in rows if proof_sql(query)]), 1)
            self.assertGreaterEqual(read.used.statements, injected[0].statements + 1)
            self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)
            self.assertTrue(any(query.startswith("RELEASE SAVEPOINT ") for query in observer.queries))
            observer.enabled = False
            self.assertEqual(uow.stores.connection.execute("SELECT 1").fetchone(), (1,))
        self.assertEqual(self.installation_snapshot(), before)

    def test_warm_context_must_preserve_entire_original_configuration_selection(self):
        from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            key, = self.proof_keys(read)
            context = read.sources[key].correlation
            selection = context.configuration_instances
            self.assertGreater(len(selection.instances), 1)
            smaller = ConfigurationInstanceSelection((source.ref,))
            statements = len(observer.queries)
            object.__setattr__(context, "configuration_instances", smaller)
            try:
                self.assert_unavailable(lambda: self.terminal(uow, source, read))
                self.assertEqual(context.configuration_instances, smaller)
                self.assertEqual(len(observer.queries), statements)
            finally:
                object.__setattr__(context, "configuration_instances", selection)

    def test_insufficient_probe_and_closure_allowance_refuses_before_opening_savepoint(self):
        intent, attempt, outcome = self.recorded_terminal()
        with self.reading(intent, attempt) as (uow, read, source, observer, _rows):
            self.assertEqual(self.terminal(uow, source, read), (outcome, attempt))
            key, = self.proof_keys(read)
            del read.sources[key]
            read.used = read.used.plus(Footprint(8192 - read.used.records, 0, 0, 0))
            prior, statements = read.used, len(observer.queries)
            with self.assertRaises(_Capacity):
                self.terminal(uow, source, read)
            self.assertEqual(read.used, prior)
            self.assertEqual(len(observer.queries), statements)
