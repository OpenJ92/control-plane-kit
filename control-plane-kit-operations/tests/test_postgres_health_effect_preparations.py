"""#1851 retained joins, caller transactions, concurrency and schema evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from tests.health_effect_preparation_fixture import RELATION, STORE_MODULE
from tests.postgres_health_effect_preparation_fixture import PostgresHealthEffectPreparationFixture


class NoSql:
    def execute(self, *args):
        raise AssertionError("malformed self-contained input reached SQL")


class RecordingCursor:
    def __init__(self, cursor, owner, query):
        self.cursor, self.owner, self.query = cursor, owner, str(query)

    def checked(self, rows):
        for row in rows:
            for value in row:
                if type(value) is bytes:
                    self.owner.byte_lengths.append(len(value))
                if type(value) is str:
                    self.owner.text_lengths.append(len(value.encode("utf-8")))
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        return None if row is None else self.checked([row])[0]

    def fetchall(self):
        rows = self.cursor.fetchall()
        self.owner.batch_sizes.append(len(rows))
        if RELATION in self.query:
            self.owner.health_batch_sizes.append(len(rows))
            names = [item.name for item in self.cursor.description]
            if all(name in names for name in ("run_id", "activity_id", "attempt")):
                positions = [names.index(name) for name in ("run_id", "activity_id", "attempt")]
                self.owner.health_scan_keys.extend(tuple(row[index] for index in positions) for row in rows)
        return self.checked(rows)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.queries, self.byte_lengths, self.batch_sizes = [], [], []
        self.text_lengths, self.health_batch_sizes, self.health_scan_keys = [], [], []

    def execute(self, query, parameters=None):
        self.queries.append(str(query))
        cursor = self.connection.execute(query) if parameters is None else self.connection.execute(query, parameters)
        return RecordingCursor(cursor, self, query)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class PostgresHealthEffectPreparationTests(PostgresHealthEffectPreparationFixture, unittest.TestCase):
    def test_restart_exact_bytes_bundle_and_duplicate_attempt_do_not_renew(self):
        record = self.persist_health()
        codec = self.api().HealthEffectPreparationCodec()
        stored = self.connection.execute(f"SELECT preimage FROM {RELATION}").fetchone()[0]
        self.assertEqual(stored, codec.encode_canonical_bytes(record))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(record.identity), record)
            self.assertIsNone(uow.stores.health_effect_preparations.insert_absent(record))
            uow.commit()
        with psycopg.connect(self.database_url) as connection:
            self.assertEqual(self.health_store(connection).get(record.identity), record)
        self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {RELATION}").fetchone(), (1,))
        self.assertEqual(self.connection.execute(f"SELECT preimage FROM {RELATION}").fetchone()[0], stored)

    def test_self_contained_input_fails_without_sql_and_relational_failure_precedes_insert(self):
        record = self.health_record()
        api = self.api()
        store = self.health_store(NoSql())
        for action in (lambda: store.get(None), lambda: store.insert_absent(None)):
            with self.assertRaises(api.HealthEffectPreparationError) as caught:
                action()
            self.assert_safe(caught.exception)
        recording = RecordingConnection(self.connection)
        missing = replace(record, transit_authorization_id="suse_" + "f" * 64)
        with self.assertRaises(api.HealthEffectPreparationError) as caught:
            self.health_store(recording).insert_absent(missing)
        self.assert_safe(caught.exception, "suse_" + "f" * 64)
        self.assertTrue(recording.queries)
        self.assertFalse(any(query.lstrip().upper().startswith("INSERT") for query in recording.queries))

    def test_caller_rollback_removes_only_new_preparation_and_preserves_existing_owners(self):
        record = self.health_record()
        with self.unit_of_work() as uow:
            self.assertEqual(self.health_store(uow.stores.connection).insert_absent(record), record)
            # No commit: the owning UOW rolls back.
        with self.assertRaises(KeyError) as caught:
            self.health_store().get(record.identity)
        self.assertNotIn(record.identity.run_id.value, str(caught.exception))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempt_intents.get(record.identity), self.health_intent)
            for family in ("transit", "workload"):
                self.assertEqual(uow.stores.secret_use_authorizations.get("workspace-a",
                    self.uses[family].authorization_id), self.uses[family])
            uow.commit()

    def test_two_connections_compete_for_one_immutable_attempt(self):
        record = self.health_record()
        store_type = self.api(STORE_MODULE).HealthEffectPreparationStore
        barrier = threading.Barrier(2, timeout=10)
        def insert():
            with psycopg.connect(self.database_url) as connection:
                connection.execute("SET LOCAL statement_timeout='15s'")
                barrier.wait()
                result = store_type(connection).insert_absent(record)
                connection.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(insert) for _ in range(2)]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(results.count(record), 1)
        self.assertEqual(results.count(None), 1)
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_expired_revoked_advanced_and_evolved_history_remains_exact(self):
        record = self.persist_health()
        evolved = self.transition(self.health_attempt, "succeeded", event_id="health-finished", ordinal=4)
        with self.unit_of_work() as uow:
            stores = uow.stores
            stores.execution.add_event(evolved.latest_transition_event)
            self.assertEqual(stores.effect_attempts.compare_and_set(self.health_attempt, evolved), evolved)
            stores.connection.execute("""UPDATE cpk_delegation_signing_keys
                SET status='revoked', revoked_by='operator-a', revoked_at='2026-08-02T00:00:00Z'
                WHERE workspace_id='workspace-a'""")
            stores.connection.execute("UPDATE cpk_secret_references SET status='revoked', revoked_by='operator-a', revoked_at='2026-08-02T00:00:00Z' WHERE workspace_id='workspace-a'")
            stores.workspaces.set_current_graph("workspace-a", "health-desired", self.values["desired_realized_projection_id"])
            self.assertEqual(self.health_store(stores.connection).get(record.identity), record)
            uow.commit()
        self.assertEqual(self.health_store().get(record.identity), record)
        self.assertEqual(record.transit_grant.expires_at, 1_700_000_060)
        self.assertEqual(record.original_event_id, self.health_attempt.original_start_event.event_id)

    def test_each_retained_family_witness_rejects_independent_drift(self):
        record = self.persist_health()
        api = self.api()
        mutations = []
        for family in ("transit", "workload"):
            key, use, reference = self.keys[family], self.uses[family], self.references[family]
            mutations.extend((
                ("key-issuer-" + family, "UPDATE cpk_delegation_signing_keys SET issuer='foreign-issuer' WHERE registration_id=%s", (key.registration_id,)),
                ("key-purpose-" + family, "UPDATE cpk_delegation_signing_keys SET purpose='gateway-probe' WHERE registration_id=%s", (key.registration_id,)),
                ("use-actor-" + family, "UPDATE cpk_secret_use_authorizations SET actor_subject='foreign-actor' WHERE authorization_id=%s", (use.authorization_id,)),
                ("use-operation-" + family, "UPDATE cpk_secret_use_authorizations SET operation_id='foreign-operation' WHERE authorization_id=%s", (use.authorization_id,)),
                ("use-correlation-" + family, "UPDATE cpk_secret_use_authorizations SET correlation_id='foreign-correlation' WHERE authorization_id=%s", (use.authorization_id,)),
                ("reference-material-" + family, "UPDATE cpk_secret_references SET secret_reference='secret://health-secrets/keys/foreign' WHERE registration_id=%s", (reference.registration_id,)),
                ("use-reference-" + family, "UPDATE cpk_secret_use_authorizations SET secret_reference='secret://health-secrets/keys/foreign' WHERE authorization_id=%s", (use.authorization_id,)),
            ))
        for label, query, parameters in mutations:
            with self.subTest(witness=label), self.unit_of_work() as uow:
                uow.stores.connection.execute(query, parameters)
                with self.assertRaises(api.HealthEffectPreparationCorrupt) as caught:
                    self.health_store(uow.stores.connection).get(record.identity)
                self.assert_safe(caught.exception, "foreign-issuer", "foreign-actor", "foreign-operation", "foreign-correlation")
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_exact_original_event_and_projection_id_are_not_replaceable_by_equal_content(self):
        record = self.persist_health()
        api = self.api()
        with self.unit_of_work() as uow:
            stores = uow.stores
            projection = stores.realized_graphs.get(self.values["desired_realized_projection_id"])
            # Alternative projection identity with the same graph descriptor needs
            # a distinct identity envelope; neither makes it the pinned plan input.
            from control_plane_kit_operations.records import RealizedGraphProjectionRecord
            alternate = RealizedGraphProjectionRecord.from_graph(projection_id="equal-content-projection",
                workspace_id=projection.workspace_id, source_authored_graph_id=projection.source_authored_graph_id,
                projection_kind=projection.projection_kind, projection_key="equal-content",
                graph=self.health_context()[3].graph, created_by="operator-a", created_at=projection.created_at)
            stores.realized_graphs.save(alternate)
            wrong = replace(record, desired_realized_projection_id=alternate.projection_id)
            with self.assertRaises(api.HealthEffectPreparationError):
                self.health_store(stores.connection).insert_absent(wrong)
        with self.unit_of_work() as uow:
            uow.stores.connection.execute("UPDATE cpk_activity_events SET payload=jsonb_set(payload,'{activity_id}',%s) WHERE event_id=%s",
                (Jsonb("foreign-activity"), record.original_event_id))
            with self.assertRaises(api.HealthEffectPreparationCorrupt) as caught:
                self.health_store(uow.stores.connection).get(record.identity)
            self.assert_safe(caught.exception, "foreign-activity")

    def test_corrupt_preimage_is_bounded_before_transfer_and_current_validation_refuses(self):
        record = self.persist_health()
        api = self.api()
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            # Remove only this disposable transaction's size check to simulate
            # retained corruption; rollback restores both schema and original row.
            checks = connection.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%octet_length(preimage)%%'", (RELATION,)).fetchall()
            self.assertEqual(len(checks), 1)
            connection.execute(psycopg.sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                psycopg.sql.Identifier(RELATION), psycopg.sql.Identifier(checks[0][0])))
            connection.execute(f"UPDATE {RELATION} SET preimage=%s", (b"x" * 16_385,))
            recording = RecordingConnection(connection)
            with self.assertRaises(api.HealthEffectPreparationCorrupt):
                self.health_store(recording).get(record.identity)
            self.assertFalse(any(size > 16_384 for size in recording.byte_lengths))
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            connection.execute(f"UPDATE {RELATION} SET preimage=%s", (b'{"private-canary":true}',))
            with self.assertRaises(SchemaInstallationError) as caught:
                install_schema(connection)
            self.assert_safe(caught.exception, "private-canary")
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_exact_current_reentry_and_unrelated_attempt_absence_are_read_only(self):
        record = self.persist_health()
        # Reuse a lawful existing nonhealth attempt, without inventing a health row.
        ordinary, intent = self.intent_attempt(activity_id="ordinary-unrelated", event_id="ordinary-original", ordinal=5)
        self.persist_evidence_chain(ordinary, intent)
        recording = RecordingConnection(self.connection)
        install_schema(recording)
        self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP"))
            for query in recording.queries))
        self.assertTrue(any(RELATION in query for query in recording.queries))
        self.assertEqual(self.health_store().get(record.identity), record)
        with self.assertRaises(KeyError):
            self.health_store().get(ordinary.state.identity)

    def test_unrelated_driver_failure_is_not_hidden_as_missing_or_success(self):
        record = self.health_record()
        class DriverFailure(RuntimeError):
            pass
        class BrokenConnection:
            def execute(self, *args):
                raise DriverFailure("unrelated-driver-failure")
        with self.assertRaises(DriverFailure):
            self.health_store(BrokenConnection()).get(record.identity)

    def test_request_and_each_family_jti_uniqueness_are_independent_of_attempt_pk(self):
        original = self.persist_health()
        retry = self.seed_retry_health_values()
        api = self.api()
        same_request = replace(retry.request, request_id=original.request.request_id)
        variants = (
            replace(retry, request=same_request,
                transit_grant=replace(retry.transit_grant, request_id=same_request.request_id,
                    request_digest=same_request.canonical_digest()),
                workload_grant=replace(retry.workload_grant, request_id=same_request.request_id,
                    request_digest=same_request.canonical_digest())),
            replace(retry, transit_grant=replace(retry.transit_grant, jti=original.transit_grant.jti)),
            replace(retry, workload_grant=replace(retry.workload_grant, jti=original.workload_grant.jti)),
        )
        for candidate in variants:
            with self.subTest(identity=candidate.identity.attempt), self.unit_of_work() as uow:
                with self.assertRaises(api.HealthEffectPreparationConflict) as caught:
                    self.health_store(uow.stores.connection).insert_absent(candidate)
                self.assert_safe(caught.exception, original.request.request_id,
                    original.transit_grant.jti, original.workload_grant.jti)
        with self.unit_of_work() as uow:
            self.assertEqual(self.health_store(uow.stores.connection).insert_absent(retry), retry)
            uow.commit()
        self.assertEqual(self.health_store().get(original.identity), original)
        self.assertEqual(self.health_store().get(retry.identity), retry)
        unique_columns = self.connection.execute("""SELECT array_agg(a.attname ORDER BY member.ordinality)
            FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY member(attnum, ordinality)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=member.attnum
            WHERE c.conrelid=%s::regclass AND c.contype='u' GROUP BY c.oid""", (RELATION,)).fetchall()
        self.assertIn((["workspace_id", "logical_request_id"],), unique_columns)
        self.assertNotIn((["logical_request_id"],), unique_columns)

    def test_retained_reference_provider_chain_rejects_self_consistent_wrong_pair(self):
        from control_plane_kit_operations.secret_providers import (
            AuthorizeSecretUse, authorized_secret_use_for,
        )
        record = self.health_record()
        api = self.api()
        provider, reference = self.alternate_provider_reference()
        for family in ("transit", "workload"):
            use = self.uses[family]
            command = AuthorizeSecretUse(workspace_id=use.workspace_id, reference=use.reference,
                intent=use.intent, actor_subject=use.actor_subject, correlation_id=use.correlation_id,
                requested_at=use.requested_at, actor_scopes=(), operation_id=use.operation_id,
                session_id=use.session_id, run_id=use.run_id, activity_id=use.activity_id)
            # Fingerprint and authorization ID are internally correct; only the
            # retained reference/material/provider chain makes this pair wrong.
            forged = authorized_secret_use_for(command, reference=reference, provider=provider)
            self.assertEqual(forged.reference, use.reference)
            self.assertNotEqual(reference.reference, forged.reference)
            self.assertEqual(forged.authorization_id, "suse_" + forged.intent_fingerprint)
            with self.subTest(family=family, case="coherent-wrong-pair"), self.unit_of_work() as uow:
                uow.stores.connection.execute("DELETE FROM cpk_secret_use_authorizations WHERE authorization_id=%s",
                    (use.authorization_id,))
                uow.stores.secret_use_authorizations.add(forged)
                candidate = replace(record, **{family + "_authorization_id": forged.authorization_id})
                with self.assertRaises(api.HealthEffectPreparationError):
                    self.health_store(uow.stores.connection).insert_absent(candidate)
            with self.subTest(family=family, case="reference-provider-drift"), self.unit_of_work() as uow:
                store = self.health_store(uow.stores.connection)
                self.assertEqual(store.insert_absent(record), record)
                uow.stores.connection.execute("UPDATE cpk_secret_references SET provider_registration_id=%s WHERE registration_id=%s",
                    (provider.registration_id, self.references[family].registration_id))
                with self.assertRaises(api.HealthEffectPreparationCorrupt):
                    store.get(record.identity)

    def test_authored_revision_cannot_be_replaced_by_projection_or_other_plan_side(self):
        record = self.health_record()
        api = self.api()
        from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole
        for revision in (self.values["desired_realized_projection_id"], "health-base", "foreign-revision"):
            target = replace(record.request.target, graph_revision=NodeControlGraphReference(
                NodeControlGraphReferenceRole.GRAPH_REVISION, revision))
            request = replace(record.request, target=target)
            candidate = replace(record, request=request,
                transit_grant=replace(record.transit_grant, target=target, request_digest=request.canonical_digest()),
                workload_grant=replace(record.workload_grant, target=target, request_digest=request.canonical_digest()))
            with self.subTest(revision=revision), self.unit_of_work() as uow:
                with self.assertRaises(api.HealthEffectPreparationError) as caught:
                    self.health_store(uow.stores.connection).insert_absent(candidate)
                self.assert_safe(caught.exception, "foreign-revision")

    def test_missing_original_attempt_is_refused_without_erasing_preparation(self):
        record = self.persist_health()
        api = self.api()
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            constraints = connection.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND confrelid='cpk_effect_attempts'::regclass AND contype='f'", (RELATION,)).fetchall()
            self.assertEqual(len(constraints), 1)
            connection.execute(psycopg.sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                psycopg.sql.Identifier(RELATION), psycopg.sql.Identifier(constraints[0][0])))
            connection.execute("DELETE FROM cpk_effect_attempts WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (record.identity.run_id.value, record.identity.activity_id, record.identity.attempt))
            with self.assertRaises(api.HealthEffectPreparationCorrupt) as caught:
                self.health_store(connection).get(record.identity)
            self.assert_safe(caught.exception)
            self.assertEqual(connection.execute(f"SELECT count(*) FROM {RELATION}").fetchone(), (1,))
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_equal_graph_content_retains_base_authored_side_and_both_projection_ids(self):
        from control_plane_kit_core.planning import PlanGraphSide
        from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole
        self.reset_start_truth()
        self.seed_health_owners(side=PlanGraphSide.BASE_GRAPH)
        record = self.persist_health()
        with self.unit_of_work() as uow:
            base = uow.stores.realized_graphs.get(record.base_realized_projection_id)
            desired = uow.stores.realized_graphs.get(record.desired_realized_projection_id)
            self.assertEqual(base.graph_descriptor, desired.graph_descriptor)
            self.assertNotEqual(base.projection_id, desired.projection_id)
            self.assertEqual(record.request.target.graph_revision.value, base.source_authored_graph_id)
            self.assertNotEqual(record.request.target.graph_revision.value, desired.source_authored_graph_id)
            uow.commit()
        target = replace(record.request.target, graph_revision=NodeControlGraphReference(
            NodeControlGraphReferenceRole.GRAPH_REVISION, "health-desired"))
        request = replace(record.request, target=target)
        wrong = replace(record, request=request,
            transit_grant=replace(record.transit_grant, target=target, request_digest=request.canonical_digest()),
            workload_grant=replace(record.workload_grant, target=target, request_digest=request.canonical_digest()))
        with self.assertRaises(self.api().HealthEffectPreparationError):
            self.health_store().insert_absent(wrong)
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_prior_schema_without_preparation_table_is_refused_intact(self):
        record = self.persist_health()
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            connection.execute(psycopg.sql.SQL("DROP TABLE {}").format(psycopg.sql.Identifier(RELATION)))
            before = connection.execute("SELECT count(*) FROM cpk_effect_attempt_intents").fetchone()
            with self.assertRaises(SchemaInstallationError) as caught:
                install_schema(connection)
            self.assertEqual(str(caught.exception), "operations schema reset is required")
            self.assertIsNone(connection.execute("SELECT to_regclass(%s)", (RELATION,)).fetchone()[0])
            self.assertEqual(connection.execute("SELECT count(*) FROM cpk_effect_attempt_intents").fetchone(), before)
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_foreign_workspace_cannot_borrow_existing_family_references(self):
        from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole
        record = self.health_record()
        target = replace(record.request.target, workspace_id=NodeControlGraphReference(
            NodeControlGraphReferenceRole.WORKSPACE, "foreign-workspace"))
        request = replace(record.request, target=target)
        candidate = replace(record, request=request,
            transit_grant=replace(record.transit_grant, target=target, request_digest=request.canonical_digest()),
            workload_grant=replace(record.workload_grant, target=target, request_digest=request.canonical_digest()))
        recording = RecordingConnection(self.connection)
        with self.assertRaises(self.api().HealthEffectPreparationError) as caught:
            self.health_store(recording).insert_absent(candidate)
        self.assert_safe(caught.exception, "foreign-workspace")
        self.assertFalse(any(query.lstrip().upper().startswith("INSERT") for query in recording.queries))

    def test_every_projected_health_target_dimension_rejects_congruent_foreign_pair(self):
        from tests.health_effect_preparation_fixture import pair_for_request
        from control_plane_kit_core.node_control import (
            NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
        )
        from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclarationIdentity
        record = self.health_record()
        request = record.request
        alternatives = (
            ("node", replace(request, target=replace(request.target, node_id=NodeControlGraphReference(
                NodeControlGraphReferenceRole.NODE, "foreign-node")))),
            ("socket", replace(request, target=replace(request.target, provider_socket_name=NodeControlGraphReference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET, "foreign-socket")))),
            ("runtime", replace(request, runtime_id=NodeControlGraphReference(
                NodeControlGraphReferenceRole.RUNTIME, "foreign-runtime"))),
            ("kind", replace(request, kind=NodeHealthReadKind.READINESS)),
            ("declaration", replace(request, declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity("f" * 64))),
        )
        candidates = [(label, pair_for_request(record, candidate)) for label, candidate in alternatives]
        candidates.append(("gateway", replace(record, transit_grant=replace(record.transit_grant,
            gateway_node_id=NodeControlGraphReference(NodeControlGraphReferenceRole.NODE, "foreign-gateway")))))
        for label, candidate in candidates:
            self.assertNotEqual(candidate, record)
            if label != "gateway":
                self.assertNotEqual(candidate.request, request)
            with self.subTest(dimension=label):
                recording = RecordingConnection(self.connection)
                with self.assertRaises(self.api().HealthEffectPreparationError) as caught:
                    self.health_store(recording).insert_absent(candidate)
                self.assert_safe(caught.exception, "foreign-node", "foreign-socket", "foreign-runtime", "foreign-gateway")
                self.assertFalse(any(query.lstrip().upper().startswith("INSERT") for query in recording.queries))

    def test_coherent_authorization_context_is_compared_with_attempt_and_other_family(self):
        from control_plane_kit_core.secrets import SecretUseIntent
        record = self.health_record()
        api = self.api()
        changes = (
            {"operation_id": "other-operation"}, {"session_id": None}, {"run_id": None},
            {"activity_id": None}, {"effect_id": "other-effect"}, {"probe_id": "other-probe"},
            {"actor_subject": "other-operator"}, {"intent": SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY},
        )
        for family in ("transit", "workload"):
            for change in changes:
                use = self.coherent_use(family, **change)
                with self.subTest(family=family, field=tuple(change)), self.unit_of_work() as uow:
                    uow.stores.secret_use_authorizations.add(use)
                    candidate = replace(record, **{family + "_authorization_id": use.authorization_id})
                    with self.assertRaises(api.HealthEffectPreparationError):
                        self.health_store(uow.stores.connection).insert_absent(candidate)
        # The record derives actor from the pair, not from current caller/request.
        with self.unit_of_work() as uow:
            changed = {}
            for family in ("transit", "workload"):
                use = self.coherent_use(family, actor_subject="other-operator")
                uow.stores.secret_use_authorizations.add(use)
                changed[family + "_authorization_id"] = use.authorization_id
            candidate = replace(record, **changed)
            self.assertEqual(self.health_store(uow.stores.connection).insert_absent(candidate), candidate)

    def test_key_identity_material_and_authorization_fingerprint_are_independent_witnesses(self):
        record = self.persist_health()
        api = self.api()
        for family in ("transit", "workload"):
            changes = (
                ("UPDATE cpk_delegation_signing_keys SET key_id='foreign-key' WHERE registration_id=%s", (self.keys[family].registration_id,)),
                ("UPDATE cpk_delegation_signing_keys SET private_key_reference='secret://health-secrets/keys/foreign' WHERE registration_id=%s", (self.keys[family].registration_id,)),
                ("UPDATE cpk_secret_use_authorizations SET intent_fingerprint=%s WHERE authorization_id=%s", ("f" * 64, self.uses[family].authorization_id)),
            )
            for query, values in changes:
                with self.subTest(family=family, query=query), self.unit_of_work() as uow:
                    uow.stores.connection.execute(query, values)
                    with self.assertRaises(api.HealthEffectPreparationCorrupt):
                        self.health_store(uow.stores.connection).get(record.identity)
        with self.unit_of_work() as uow:
            same = self.keys["transit"].public_key
            uow.stores.connection.execute("UPDATE cpk_delegation_signing_keys SET public_key_pem=%s, public_fingerprint_sha256=%s WHERE registration_id=%s",
                (same.public_key_pem, same.fingerprint_sha256, self.keys["workload"].registration_id))
            with self.assertRaises(api.HealthEffectPreparationCorrupt):
                self.health_store(uow.stores.connection).get(record.identity)

    def test_nonhealth_original_intent_cannot_own_a_health_preparation(self):
        from control_plane_kit_core.planning import StartNode
        record = self.health_record()
        plan = self.health_context()[0]
        activity = next(item for item in plan.activities
            if type(item.operation) is StartNode and item.operation.target.node_id == "api")
        intent = replace(self.health_intent.intent, activity_id=activity.activity_id, operation=activity.operation)
        attempt, evidence = self.intent_attempt(intent=intent, activity_id=activity.activity_id.value,
            event_id="nonhealth-original", ordinal=5)
        self.persist_evidence_chain(attempt, evidence)
        self.health_attempt, self.health_intent, self.health_activity = attempt, evidence, activity
        from tests.health_effect_preparation_fixture import wire_identity
        self.values = {**self.values, "identity": evidence.identity,
            "request_fingerprint": evidence.request_fingerprint, "original_event_id": evidence.original_start_event.event_id,
            "transit_grant": replace(record.transit_grant, attempt_id=wire_identity(evidence.identity))}
        self.seed_health_authority()
        candidate = self.health_record()
        with self.assertRaises(self.api().HealthEffectPreparationError):
            self.health_store().insert_absent(candidate)

    def test_projection_content_drift_rederives_the_original_operation_pins(self):
        from control_plane_kit_operations.records import RealizedGraphProjectionRecord
        record = self.persist_health()
        with self.unit_of_work() as uow:
            stores = uow.stores
            prior = stores.realized_graphs.get(record.desired_realized_projection_id)
            changed = RealizedGraphProjectionRecord.from_graph(
                projection_id=prior.projection_id, workspace_id=prior.workspace_id,
                source_authored_graph_id=prior.source_authored_graph_id,
                projection_kind=prior.projection_kind, projection_key=prior.projection_key,
                graph=replace(self.health_context()[3].graph, name="changed-projection-material"),
                created_by=prior.created_by, created_at=prior.created_at)
            self.assertNotEqual(changed.projection_digest, prior.projection_digest)
            stores.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s, projection_digest=%s WHERE projection_id=%s",
                (Jsonb(changed.graph_descriptor), changed.projection_digest, prior.projection_id))
            self.assertEqual(stores.realized_graphs.get(prior.projection_id), changed)
            with self.assertRaises(self.api().HealthEffectPreparationCorrupt):
                self.health_store(stores.connection).get(record.identity)

    def test_canonical_preimage_and_each_indexed_witness_are_checked_independently(self):
        from tests.health_effect_preparation_fixture import pair_for_request
        record = self.persist_health()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        other_use = self.coherent_use("transit", actor_subject="other-operator")
        with self.unit_of_work() as uow:
            uow.stores.secret_use_authorizations.add(other_use)
            uow.commit()
        for column, value in (
            ("logical_request_id", "other-logical-request"),
            ("transit_jti", "other-transit-jti"),
            ("desired_realized_projection_id", record.base_realized_projection_id),
            ("transit_authorization_id", other_use.authorization_id),
        ):
            with self.subTest(column=column), self.unit_of_work() as uow:
                uow.stores.connection.execute(psycopg.sql.SQL("UPDATE {} SET {}=%s").format(
                    psycopg.sql.Identifier(RELATION), psycopg.sql.Identifier(column)), (value,))
                with self.assertRaises(api.HealthEffectPreparationCorrupt):
                    self.health_store(uow.stores.connection).get(record.identity)
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            constraints = connection.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND confrelid='cpk_effect_attempt_intents'::regclass AND contype='f'", (RELATION,)).fetchall()
            self.assertEqual(len(constraints), 1)
            connection.execute(psycopg.sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                psycopg.sql.Identifier(RELATION), psycopg.sql.Identifier(constraints[0][0])))
            connection.execute(f"UPDATE {RELATION} SET original_event_id='other-original-event'")
            with self.assertRaises(api.HealthEffectPreparationCorrupt):
                self.health_store(connection).get(record.identity)
        changed_request = pair_for_request(record, replace(record.request, request_id="other-canonical-request"))
        changed_grant = replace(record, transit_grant=replace(record.transit_grant, jti="other-canonical-jti"))
        for changed in (changed_request, changed_grant):
            canonical = codec.encode_canonical_bytes(changed)
            self.assertEqual(codec.decode_canonical_bytes(canonical), changed)
            with self.subTest(canonical_only=True), self.unit_of_work() as uow:
                uow.stores.connection.execute(f"UPDATE {RELATION} SET preimage=%s", (canonical,))
                with self.assertRaises(api.HealthEffectPreparationCorrupt):
                    self.health_store(uow.stores.connection).get(record.identity)
        self.assertEqual(self.health_store().get(record.identity), record)

    def test_current_scan_visits_bounded_pages_and_detects_corruption_beyond_first_page(self):
        original = self.persist_health()
        records = [original]
        for _ in range(8):
            record = self.seed_retry_health_values()
            with self.unit_of_work() as uow:
                self.assertEqual(self.health_store(uow.stores.connection).insert_absent(record), record)
                uow.commit()
            records.append(record)
        validator = self.api(STORE_MODULE)._validate_current_rows
        recording = RecordingConnection(self.connection)
        validator(recording)
        self.assertTrue(recording.health_batch_sizes)
        self.assertTrue(all(size <= 8 for size in recording.health_batch_sizes))
        self.assertEqual(sum(recording.health_batch_sizes), len(records))
        self.assertGreaterEqual(len([size for size in recording.health_batch_sizes if size]), 2)
        self.assertFalse(any(size > 16_384 for size in recording.byte_lengths))
        # Keyset order of one run/activity follows its integer attempt, not a
        # string serialization that would reorder retries or repeat a page.
        self.assertEqual(recording.health_scan_keys, [(item.identity.run_id.value, item.identity.activity_id, item.identity.attempt) for item in records])
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            connection.execute(f"UPDATE {RELATION} SET preimage=%s WHERE attempt=9", (b'{"last-page-canary":true}',))
            recording = RecordingConnection(connection)
            with self.assertRaises(self.api().HealthEffectPreparationError) as caught:
                validator(recording)
            self.assert_safe(caught.exception, "last-page-canary")
            self.assertTrue(all(size <= 8 for size in recording.health_batch_sizes))
            self.assertGreater(sum(recording.health_batch_sizes), 8)
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            checks = connection.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%logical_request_id%%'", (RELATION,)).fetchall()
            self.assertTrue(checks)
            for (name,) in checks:
                connection.execute(psycopg.sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                    psycopg.sql.Identifier(RELATION), psycopg.sql.Identifier(name)))
            connection.execute(f"UPDATE {RELATION} SET logical_request_id=%s WHERE attempt=9", ("x" * 4096,))
            recording = RecordingConnection(connection)
            with self.assertRaises(self.api().HealthEffectPreparationError):
                validator(recording)
            self.assertFalse(any(size > 2048 for size in recording.text_lengths))
        self.assertEqual(self.health_store().get(original.identity), original)

    def test_coherent_stored_plan_and_intent_cannot_invent_the_relation_digest(self):
        self.health_record()  # lawful owner setup precedes the missing-feature guard
        self.reset_start_truth()
        # Both stored descriptors and the original event fingerprint are coherent;
        # the retained executable graphs alone expose the false relation pin.
        self.seed_health_owners(relation_digest="f" * 64)
        record = self.health_record()
        self.assertEqual(self.health_intent.intent.operation.target.relation_digest, "f" * 64)
        with self.assertRaises(self.api().HealthEffectPreparationError):
            self.health_store().insert_absent(record)
