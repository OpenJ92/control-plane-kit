from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture
from tests.postgres_effect_attempt_intent_store_fixture import (
    EffectAttemptIntentStore,
    PostgresEffectAttemptIntentStoreFixture,
    RELATION,
    _validate_current_rows,
    store_module,
)


ROW_ERROR = "effect attempt intent row is invalid"


class _TransportCursor:
    def __init__(self, cursor, query, oversized) -> None:
        self.cursor = cursor
        self.query = str(query)
        self.oversized = oversized

    def _record(self, rows) -> None:
        if RELATION not in self.query:
            return
        for row in rows:
            for value in row:
                if type(value) is bytes and len(value) > 1_048_576:
                    self.oversized.append((self.query, len(value)))

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            self._record((row,))
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        self._record(rows)
        return rows

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class _TransportConnection:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.oversized: list[tuple[str, int]] = []
        self.fetchall_queries: list[str] = []

    def execute(self, query, parameters=None):
        cursor = (
            self.connection.execute(query)
            if parameters is None
            else self.connection.execute(query, parameters)
        )
        wrapped = _TransportCursor(cursor, query, self.oversized)
        original = wrapped.fetchall

        def fetchall():
            self.fetchall_queries.append(" ".join(str(query).split()))
            return original()

        wrapped.fetchall = fetchall
        return wrapped

    def __getattr__(self, name):
        return getattr(self.connection, name)


class PostgresEffectAttemptIntentStoreTests(
    PostgresEffectAttemptIntentStoreFixture,
    unittest.TestCase,
):
    def assert_row_error(self, operation, *canaries: str) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            operation()
        self.assertEqual(str(caught.exception), ROW_ERROR)
        self.assert_safe_error(caught.exception, *canaries)

    def test_lawful_semantic_maximum_roundtrips_on_a_fresh_connection(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        intent = self.largest_lawful_intent()
        attempt, record = self.intent_attempt(
            activity_id=intent.activity_id.value,
            intent=intent,
        )
        self.persist_evidence_chain(attempt, record)
        connection = psycopg.connect(self.database_url)
        try:
            loaded = EffectAttemptIntentStore(connection).get(record.identity)
        finally:
            connection.close()
        self.assertEqual(loaded, record)
        self.assertEqual(loaded.intent, intent)
        self.assertEqual(loaded.request_fingerprint, attempt.state.request_fingerprint)

    def test_strict_preimage_reconstruction_rejects_semantic_and_json_drift(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, record = self.intent_attempt()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.assertEqual(
                stores.execution.add_event(attempt.original_start_event),
                attempt.original_start_event,
            )
            self.assertEqual(
                stores.effect_attempt_intents.insert(record),
                record,
            )
            unit_of_work.commit()
        valid = self.connection.execute(
            f"SELECT preimage FROM {RELATION}"
        ).fetchone()[0]
        candidates = (
            ("utf8", b"\xff"),
            ("duplicate", valid.replace(b'{"activity_id"', b'{"activity_id":"x","activity_id"', 1)),
            ("nonfinite", valid.replace(b"8000", b"NaN", 1)),
            ("bom", b"\xef\xbb\xbf" + valid),
            ("whitespace", b" " + valid),
            ("trailing", valid + b"\n"),
            ("nonobject", b"[]"),
        )
        for label, candidate in candidates:
            with self.subTest(label=label):
                self.connection.execute(
                    f"UPDATE {RELATION} SET preimage=%s",
                    (candidate,),
                )
                self.assert_row_error(
                    lambda: EffectAttemptIntentStore(self.connection).get(record.identity),
                    label,
                    "activity_id",
                )
                self.connection.execute(
                    f"UPDATE {RELATION} SET preimage=%s",
                    (valid,),
                )

        alternate_intents = (
            (
                "workspace",
                replace(
                    record.intent,
                    source=replace(
                        record.intent.source,
                        workspace_id="workspace-foreign",
                    ),
                ),
                "workspace_id",
            ),
            (
                "request",
                replace(
                    record.intent,
                    source=replace(
                        record.intent.source,
                        request_id="request-foreign",
                    ),
                ),
                "request_id",
            ),
            ("content", self.intent(products=()), "request_fingerprint"),
        )
        for label, candidate, drifted_coordinate in alternate_intents:
            with self.subTest(copied_column_drift=label):
                alternate = EffectAttemptIntentRecord(
                    record.identity,
                    record.original_start_event,
                    candidate,
                )
                self.assertEqual(
                    EffectAttemptIntentFixture.public_round_trip(self, candidate),
                    candidate,
                )
                self.assertEqual(alternate.intent, candidate)
                copied_fingerprint = (
                    record.request_fingerprint
                    if label == "content"
                    else alternate.request_fingerprint
                )
                self.connection.execute(
                    f"UPDATE {RELATION} SET preimage=%s, request_fingerprint=%s",
                    (
                        EffectAttemptIntentFixture.canonical_bytes(self, candidate),
                        copied_fingerprint,
                    ),
                )
                copied_coordinates = self.connection.execute(
                    f"SELECT workspace_id, request_id, request_fingerprint "
                    f"FROM {RELATION}"
                ).fetchone()
                candidate_coordinates = (
                    alternate.workspace_id,
                    alternate.request_id,
                    alternate.request_fingerprint,
                )
                self.assertEqual(
                    tuple(
                        name
                        for name, copied, decoded in zip(
                            ("workspace_id", "request_id", "request_fingerprint"),
                            copied_coordinates,
                            candidate_coordinates,
                            strict=True,
                        )
                        if copied != decoded
                    ),
                    (drifted_coordinate,),
                )
                self.assert_row_error(
                    lambda: EffectAttemptIntentStore(self.connection).get(
                        record.identity
                    ),
                    label,
                )
                self.connection.execute(
                    f"UPDATE {RELATION} SET preimage=%s, request_fingerprint=%s",
                    (valid, record.request_fingerprint),
                )

    def test_transport_bounds_gate_before_python_decode_on_get_and_current(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, record = self.intent_attempt()
        self.persist_evidence_chain(attempt, record)
        for size, reaches_decode in (
            (0, False),
            (1_048_576, True),
            (1_048_577, False),
        ):
            for boundary in ("get", "current"):
                with self.subTest(size=size, boundary=boundary):
                    connection = psycopg.connect(self.database_url)
                    try:
                        connection.execute(
                            f"ALTER TABLE {RELATION} DROP CONSTRAINT "
                            "cpk_effect_attempt_intents_preimage_check"
                        )
                        connection.execute(
                            f"UPDATE {RELATION} SET preimage=%s",
                            (b"x" * size,),
                        )
                        ledger: list[bytes] = []

                        def decode(candidate):
                            ledger.append(candidate)
                            raise OperationsRecordError("decode-boundary-canary")

                        traced = _TransportConnection(connection)
                        with mock.patch.object(
                            store_module,
                            "_decode_runtime_effect_intent",
                            decode,
                        ):
                            with self.assertRaises(OperationsRecordError):
                                if boundary == "get":
                                    EffectAttemptIntentStore(traced).get(record.identity)
                                else:
                                    _validate_current_rows(traced)
                        self.assertEqual(len(ledger), 1 if reaches_decode else 0)
                        if reaches_decode:
                            self.assertEqual(len(ledger[0]), size)
                        self.assertEqual(traced.oversized, [])
                    finally:
                        connection.rollback()
                        connection.close()

    def test_current_validation_uses_exact_eight_row_keyset_pages(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        for index in range(17):
            attempt, evidence = self.indexed_intent_attempt(index)
            self.persist_evidence_chain(attempt, evidence)
        traced = _TransportConnection(self.connection)
        _validate_current_rows(traced)
        page_queries = [
            value for value in traced.fetchall_queries if RELATION in value
        ]
        self.assertEqual(len(page_queries), 3)
        for query in page_queries:
            self.assertIn("LIMIT %s", query)
            self.assertNotIn("OFFSET", query.upper())
        for query in page_queries[1:]:
            self.assertIn(
                "(intent.run_id, intent.activity_id, intent.attempt) "
                "> (%s, %s, %s)",
                query,
            )

        middle_preimage = self.connection.execute(
            f"SELECT preimage FROM {RELATION} "
            "WHERE activity_id='intent-activity-008'"
        ).fetchone()[0]
        self.connection.execute(
            f"UPDATE {RELATION} SET preimage='{{}}'::bytea "
            "WHERE activity_id='intent-activity-008'"
        )
        traced = _TransportConnection(self.connection)
        self.assert_row_error(lambda: _validate_current_rows(traced))
        self.assertEqual(
            len([value for value in traced.fetchall_queries if RELATION in value]),
            2,
        )

        self.connection.execute(
            f"UPDATE {RELATION} SET preimage='{{}}'::bytea "
            "WHERE activity_id='intent-activity-016'"
        )
        self.connection.execute(
            f"UPDATE {RELATION} SET preimage=%s "
            "WHERE activity_id='intent-activity-008'",
            (middle_preimage,),
        )
        traced = _TransportConnection(self.connection)
        self.assert_row_error(lambda: _validate_current_rows(traced))
        self.assertEqual(
            len([value for value in traced.fetchall_queries if RELATION in value]),
            3,
        )

    def test_exact_acknowledgement_and_driver_fault_partition(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, record = self.intent_attempt()
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.execution.add_event(
                    attempt.original_start_event
                ),
                attempt.original_start_event,
            )
            inserted = unit_of_work.stores.effect_attempt_intents.insert(record)
            self.assertIs(type(inserted), EffectAttemptIntentRecord)
            self.assertEqual(inserted, record)

        self.persist_evidence_chain(attempt, record)
        store = EffectAttemptIntentStore(self.connection)
        for error in (
            TypeError("intent-codec-type-canary"),
            RuntimeError("intent-codec-runtime-canary"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    store_module,
                    "_decode_runtime_effect_intent",
                    side_effect=error,
                ):
                    with self.assertRaises(type(error)) as caught:
                        store.get(record.identity)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
