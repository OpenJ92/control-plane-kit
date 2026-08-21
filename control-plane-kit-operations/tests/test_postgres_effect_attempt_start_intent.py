from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict,
    ExistingAttempt,
    NewlyStarted,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_intent_store_fixture import (
    EffectAttemptIntentStore,
    PostgresEffectAttemptIntentStoreFixture,
    RELATION,
    store_module,
)
from tests.postgres_effect_attempt_start_fixture import REPLAY_ERROR, SERIALIZATION_ERROR


class PostgresEffectAttemptStartIntentTests(
    PostgresEffectAttemptIntentStoreFixture,
    unittest.TestCase,
):
    def test_ungated_stage_one_values_and_predecessor_truth_are_lawful(self) -> None:
        command = self.start_command()
        _attempt, record = self.intent_attempt(intent=command.intent)
        self.assertEqual(record.identity, command.transition.identity)
        self.assertEqual(record.intent, command.intent)
        self.assertEqual(
            record.request_fingerprint,
            command.transition.request_fingerprint,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT request_id, workspace_id FROM cpk_execution_requests "
                "WHERE request_id='request-a'"
            ).fetchone(),
            ("request-a", "workspace-a"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT run_id, request_id FROM cpk_activity_runs "
                "WHERE run_id='run-a'"
            ).fetchone(),
            ("run-a", "request-a"),
        )

    def test_first_start_writes_event_then_evidence_then_attempt_then_commit(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        calls: list[str] = []
        original_event = PostgresExecutionStore.add_event
        original_evidence = EffectAttemptIntentStore.insert
        original_attempt = EffectAttemptStore.insert_absent

        def add_event(store, event):
            calls.append("event")
            return original_event(store, event)

        def add_evidence(store, record):
            calls.append("evidence")
            self.assertEqual(calls, ["event", "evidence"])
            self.assertEqual(record.original_start_event.event_id, "ordered-intent-start")
            return original_evidence(store, record)

        def add_attempt(store, record):
            calls.append("attempt")
            self.assertEqual(calls, ["event", "evidence", "attempt"])
            return original_attempt(store, record)

        def unit_of_work():
            value = self.unit_of_work()
            original_commit = value.commit

            def commit():
                calls.append("commit")
                return original_commit()

            value.commit = commit
            return value

        with mock.patch.object(PostgresExecutionStore, "add_event", add_event), mock.patch.object(
            EffectAttemptIntentStore, "insert", add_evidence
        ), mock.patch.object(EffectAttemptStore, "insert_absent", add_attempt):
            result = EffectAttemptStartService(
                unit_of_work,
                id_factory=Sequence("ordered-intent-start"),
            ).execute(self.start_command())

        self.assertIs(type(result), NewlyStarted)
        self.assertEqual(calls, ["event", "evidence", "attempt", "commit"])
        with self.unit_of_work() as fresh:
            evidence = fresh.stores.effect_attempt_intents.get(
                result.attempt.state.identity
            )
        self.assertEqual(evidence.intent, self.start_command().intent)
        self.assertEqual(evidence.original_start_event, result.attempt.original_start_event)

    def test_exact_restart_replay_loads_full_evidence_before_clock_or_writes(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        current = self.persisted_started(event_id="replay-intent-start")
        calls: list[str] = []
        original_get = EffectAttemptIntentStore.get

        def get(store, identity):
            calls.append("evidence")
            return original_get(store, identity)

        ids = Sequence("replay-must-not-allocate")
        before = self.attempt_snapshot()
        with mock.patch.object(EffectAttemptIntentStore, "get", get), self.reject_database_observation(
            "exact replay sampled database time"
        ), mock.patch.object(
            PostgresExecutionStore,
            "add_event",
            side_effect=AssertionError("exact replay wrote an event"),
        ), mock.patch.object(
            EffectAttemptIntentStore,
            "insert",
            side_effect=AssertionError("exact replay wrote intent evidence"),
        ), mock.patch.object(
            EffectAttemptStore,
            "insert_absent",
            side_effect=AssertionError("exact replay wrote an attempt"),
        ):
            result = self.start_service_with_id_factory(ids).execute(self.start_command())
        self.assertEqual(result, ExistingAttempt(current))
        self.assertEqual(calls, ["evidence"])
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_missing_or_corrupt_evidence_conflicts_before_clock_ids_or_writes(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        current = self.persisted_started(event_id="invalid-intent-start")
        incongruent = EffectAttemptIntentRecord(
            current.state.identity,
            current.original_start_event,
            self.intent(products=()),
        )
        cases = (
            ("missing", KeyError("missing-intent-canary"), None),
            (
                "corrupt",
                OperationsRecordError("effect attempt intent row is invalid"),
                None,
            ),
            ("foreign", None, incongruent),
        )
        before = self.attempt_snapshot()
        for case, error, candidate in cases:
            with self.subTest(case=case):
                get_patch = (
                    mock.patch.object(EffectAttemptIntentStore, "get", side_effect=error)
                    if error is not None
                    else mock.patch.object(
                        EffectAttemptIntentStore,
                        "get",
                        return_value=candidate,
                    )
                )
                ids = Sequence(f"{case}-must-not-allocate")
                with get_patch, self.reject_database_observation(
                    f"{case} replay sampled database time"
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "add_event",
                    side_effect=AssertionError(f"{case} replay wrote an event"),
                ), mock.patch.object(
                    EffectAttemptIntentStore,
                    "insert",
                    side_effect=AssertionError(
                        f"{case} replay wrote intent evidence"
                    ),
                ), mock.patch.object(
                    EffectAttemptStore,
                    "insert_absent",
                    side_effect=AssertionError(f"{case} replay wrote an attempt"),
                ):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(
                            self.start_command()
                        )
                self.assertEqual(str(caught.exception), REPLAY_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(current.state.identity, self.start_command().transition.identity)

    def test_changed_acknowledgements_and_failures_roll_back_the_complete_chain(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        boundaries = ("event", "evidence", "attempt", "commit")
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.reset_start_truth()
                before = self.attempt_snapshot()
                sentinel = RuntimeError(f"{boundary}-raw-canary")
                patches = []
                if boundary == "event":
                    patches.append(
                        mock.patch.object(PostgresExecutionStore, "add_event", side_effect=sentinel)
                    )
                elif boundary == "evidence":
                    patches.append(
                        mock.patch.object(EffectAttemptIntentStore, "insert", side_effect=sentinel)
                    )
                elif boundary == "attempt":
                    patches.append(
                        mock.patch.object(EffectAttemptStore, "insert_absent", side_effect=sentinel)
                    )

                stack = nullcontext()
                for patcher in patches:
                    stack = _NestedContext(stack, patcher)

                if boundary == "commit":
                    def unit_of_work():
                        value = self.unit_of_work()
                        value.commit = mock.Mock(side_effect=sentinel)
                        return value
                else:
                    unit_of_work = self.unit_of_work
                with stack:
                    with self.assertRaises(RuntimeError) as caught:
                        EffectAttemptStartService(
                            unit_of_work,
                            id_factory=Sequence(f"{boundary}-event"),
                        ).execute(self.start_command())
                self.assertIs(caught.exception, sentinel)
                self.assertEqual(self.attempt_snapshot(), before)

    def test_changed_write_acknowledgements_are_fixed_conflicts_and_roll_back(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        original = {
            "event": PostgresExecutionStore.add_event,
            "evidence": EffectAttemptIntentStore.insert,
            "attempt": EffectAttemptStore.insert_absent,
        }
        owners = {
            "event": (PostgresExecutionStore, "add_event"),
            "evidence": (EffectAttemptIntentStore, "insert"),
            "attempt": (EffectAttemptStore, "insert_absent"),
        }
        for boundary in ("event", "evidence", "attempt"):
            with self.subTest(boundary=boundary):
                self.reset_start_truth()
                before = self.attempt_snapshot()
                canary = (
                    "changed-attempt-acknowledgement-canary"
                    if boundary == "attempt"
                    else boundary
                )

                def changed(store, value, *, boundary=boundary):
                    original[boundary](store, value)
                    return canary if boundary == "attempt" else object()

                owner, name = owners[boundary]
                with mock.patch.object(owner, name, changed):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service(f"changed-{boundary}-event").execute(
                            self.start_command()
                        )
                self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
                self.assert_safe_error(caught.exception, canary)
                self.assertEqual(self.attempt_snapshot(), before)

    def test_identical_race_has_one_evidence_row_and_incompatible_replay_conflicts(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        first = self.start_service("race-intent-start").execute(self.start_command())
        self.assertIs(type(first), NewlyStarted)
        replay = self.start_service("race-must-not-allocate").execute(
            self.start_command()
        )
        self.assertIs(type(replay), ExistingAttempt)
        self.assertEqual(
            self.connection.execute(f"SELECT count(*) FROM {RELATION}").fetchone(),
            (1,),
        )

        foreign = replace(
            self.start_command().intent,
            products=(),
        )
        foreign_transition = self.transition(intent=foreign)
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            self.start_service("incompatible-must-not-allocate").execute(
                self.start_command(
                    intent=foreign,
                    transition=foreign_transition,
                )
            )
        self.assertEqual(str(caught.exception), REPLAY_ERROR)


class _NestedContext:
    def __init__(self, outer, inner) -> None:
        self.outer = outer
        self.inner = inner

    def __enter__(self):
        self.outer.__enter__()
        return self.inner.__enter__()

    def __exit__(self, *arguments):
        suppress_inner = self.inner.__exit__(*arguments)
        suppress_outer = self.outer.__exit__(*arguments)
        return suppress_inner or suppress_outer


if __name__ == "__main__":
    unittest.main()
