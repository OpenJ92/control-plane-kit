from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
import unittest

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.lifecycle import (
    CancelActivityRun,
    ClaimAndOpenActivityRun,
    CompleteActivityRun,
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
    FailActivityRun,
    PauseActivityRun,
    ResumeActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
    RunLifecycleError,
    RunLifecycleNotFound,
    StartActivityRun,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.read_pages import (
    PlanReadScope,
    ReadCollection,
    ReadPageError,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    OperationActionRecord,
    OperationsRecordError,
    OperationSessionStatus,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class _TextSubclass(str):
    pass


INVALID_RUN_IDS = (
    (object(), ()),
    (True, ("True",)),
    (_TextSubclass("subclass-canary"), ("subclass-canary",)),
    ("", ()),
    (" ", ()),
    ("-leading-canary", ("leading-canary",)),
    (".leading-canary", ("leading-canary",)),
    ("_leading-canary", ("leading-canary",)),
    (":leading-canary", ("leading-canary",)),
    ("slash/canary", ("slash/canary",)),
    ("space canary", ("space canary",)),
    *tuple(
        (f"a{chr(code)}control-canary", ("control-canary",))
        for code in (*range(32), 127)
    ),
    ("a" * 201, ("a" * 32,)),
)


def _request(status: ExecutionRequestStatus = ExecutionRequestStatus.QUEUED):
    claim = (
        ClaimIdentity("worker-a", 1, "claimed", "lease")
        if status is ExecutionRequestStatus.CLAIMED
        else None
    )
    return ExecutionRequestRecord(
        ExecutionRequestIdentity("request-a", "workspace-a", "session-a", "plan-a"),
        status,
        "operator-a",
        "requested",
        "approval-request-a",
        "approval-decision-a",
        ExecutionIdempotency("execute-a", "fingerprint-a"),
        claim,
    )


def _run(run_id: object = "run-a", *, request_id="request-a", prior=None):
    return ActivityRunRecord(
        run_id,  # type: ignore[arg-type]
        "plan-a",
        AdmittedRun(request_id),
        RetryIdentity(1 if prior is None else 2, prior),  # type: ignore[arg-type]
        ActivityRunStatus.CLAIMED,
        "created",
    )


def _event(run_id: object = "run-a"):
    return ActivityEventRecord(
        "event-a",
        run_id,  # type: ignore[arg-type]
        1,
        ActivityEventKind.RUN_OPENED,
        "occurred",
    )


def _authority():
    return ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))


def _claim_command():
    return ClaimAndOpenActivityRun(
        "request-a",
        _authority(),
        ExecutionLeaseDuration(600),
        IdempotencyKey("claim-a"),
    )


def _replay_action(command, *, run_id="run-a", event_id="event-a"):
    value = {
        "command": LifecycleOperationKind.CLAIM_RUN.value,
        "request_id": command.request_id,
        "worker_id": command.authority.worker_id,
        "lease_duration_seconds": command.lease_duration.seconds,
    }
    fingerprint = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OperationActionRecord(
        "action-a",
        "session-a",
        1,
        LifecycleOperationKind.CLAIM_RUN,
        "worker-a",
        {"run_id": run_id, "event_id": event_id, "claim_generation": 1},
        "created",
        "claim-a",
        fingerprint,
    )


class _Connection:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))
        if self.error is not None:
            raise self.error
        raise AssertionError("malformed run identity reached SQL")


class _Trace:
    def __init__(
        self,
        *,
        reread=None,
        runs=(),
        action=None,
        run=None,
        event=None,
        missing_replay=False,
        missing_locator_run=False,
        missing_locked_run=False,
        ids=("run-a", "event-a", "action-a"),
        factory_error=None,
    ) -> None:
        self.log = []
        locator = (
            _request(ExecutionRequestStatus.CLAIMED)
            if action is not None
            else _request()
        )
        self.requests = [locator, reread or _request()]
        self.request_reads = 0
        self.runs = runs
        self.action = action
        self.run = run
        self.event = event
        self.missing_replay = missing_replay
        self.missing_locator_run = missing_locator_run
        self.missing_locked_run = missing_locked_run
        self.ids = list(ids)
        self.factory_error = factory_error
        self.factory_calls = 0
        self.stores = SimpleNamespace(execution=self, activity_history=self)

    def __enter__(self):
        self.log.append("uow_enter")
        return self

    def __exit__(self, *args):
        self.log.append("uow_exit")

    def commit(self):
        self.log.append("commit")

    def next_id(self):
        self.factory_calls += 1
        self.log.append(f"id_factory:{self.factory_calls}")
        if self.factory_error is not None:
            raise self.factory_error
        return self.ids.pop(0)

    def get_request(self, request_id):
        self.log.append("get_request")
        value = self.requests[min(self.request_reads, 1)]
        self.request_reads += 1
        return value

    def lock_action_idempotency(self, *args):
        self.log.append("lock_action_idempotency")

    def action_for_idempotency(self, *args):
        self.log.append("action_for_idempotency")
        return self.action

    def get_session_for_update(self, session_id):
        self.log.append("get_session_for_update")
        return SimpleNamespace(session_id=session_id, status=OperationSessionStatus.OPEN)

    def runs_for_request(self, request_id):
        self.log.append("runs_for_request")
        return self.runs

    def claim_request(self, *args):
        self.log.append("claim_request")
        return _request(ExecutionRequestStatus.CLAIMED)

    def add_run(self, record):
        self.log.append("add_run")
        return record

    def next_event_ordinal(self, run_id):
        self.log.append("next_event_ordinal")
        return 1

    def add_event(self, record):
        self.log.append("add_event")
        return record

    def next_action_ordinal(self, session_id):
        self.log.append("next_action_ordinal")
        return 1

    def add_action(self, record):
        self.log.append("add_action")
        return record

    def get_run(self, run_id):
        self.log.append("get_run")
        if self.missing_locator_run:
            raise KeyError("missing run 'locator-run-canary'")
        if self.missing_replay:
            raise KeyError("missing run 'run-secret-canary'")
        return self.run

    def get_run_for_update(self, run_id):
        self.log.append("get_run_for_update")
        if self.missing_locked_run:
            raise KeyError("missing run 'locked-run-canary'")
        return self.run

    def get_event(self, event_id):
        self.log.append("get_event")
        if self.missing_replay:
            raise KeyError("missing event 'event-secret-canary'")
        return self.event


class AuthoritativeRunIdentityTests(unittest.TestCase):
    def assert_bounded(self, error_type, callback, *canaries):
        with self.assertRaises(error_type) as captured:
            callback()
        error = captured.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)
        return error

    def service(self, trace):
        return RunLifecycleCommandService(
            lambda: trace,
            clock=lambda: "now",
            id_factory=trace.next_id,
        )

    def test_record_and_command_boundaries_share_exact_canonical_law(self):
        authority = _authority()
        key = IdempotencyKey("command-a")
        failure = FailureEvidence(
            FailureCategory.TERMINAL, "failure-a", "terminal failure"
        )
        record_factories = (
            lambda value: _run(value),
            lambda value: _run("run-current", prior=value),
            lambda value: _event(value),
        )
        command_factories = (
            lambda value: StartActivityRun(value, authority, key),
            lambda value: PauseActivityRun(value, authority, key),
            lambda value: ResumeActivityRun(value, authority, key),
            lambda value: CompleteActivityRun(value, authority, key),
            lambda value: FailActivityRun(value, authority, key, failure),
            lambda value: CancelActivityRun(value, authority, key),
        )
        for value in ("a", "r" * 200):
            for factory in record_factories + command_factories:
                with self.subTest(valid=value[:8], factory=factory.__code__.co_firstlineno):
                    factory(value)
        for value, canaries in INVALID_RUN_IDS:
            for error_type, factories in (
                (OperationsRecordError, record_factories),
                (InvalidOperationCommand, command_factories),
            ):
                for factory in factories:
                    with self.subTest(
                        invalid=type(value).__name__,
                        factory=factory.__code__.co_firstlineno,
                    ):
                        self.assert_bounded(
                            error_type,
                            lambda factory=factory, value=value: factory(value),
                            *canaries,
                        )

    def test_retry_current_and_prior_are_distinct_and_journal_is_exact(self):
        self.assert_bounded(
            OperationsRecordError,
            lambda: _run("run-a", prior="run-a"),
        )
        event = ActivityEventRecord(
            "event-a",
            "r" * 200,
            1,
            ActivityEventKind.STEP_STARTED,
            "occurred",
            activity_id="activity-a",
        )
        self.assertEqual(activity_journal_events((event,))[0].run_id, "r" * 200)

    def test_store_selectors_share_full_matrix_before_sql(self):
        direct = (
            lambda store, value: store.get_run(value),
            lambda store, value: store.get_run_for_update(value),
            lambda store, value: store.compare_and_set_run_status(
                value,
                expected=ActivityRunStatus.CLAIMED,
                replacement=ActivityRunStatus.RUNNING,
            ),
            lambda store, value: store.next_event_ordinal(value),
            lambda store, value: store.events_for_run(value),
        )
        page = (
            lambda store, value: store.event_page(
                SimpleNamespace(
                    collection=ReadCollection.RUN_EVENTS,
                    scope=SimpleNamespace(run_id=value),
                    limit=1,
                    cursor=None,
                )
            ),
            lambda store, value: store.run_page(
                SimpleNamespace(
                    collection=ReadCollection.PLAN_RUNS,
                    scope=PlanReadScope("workspace-a", "plan-a"),
                    limit=1,
                    cursor=SimpleNamespace(
                        instant="2026-08-14T00:00:00.000000Z",
                        item_id=value,
                    ),
                )
            ),
        )
        for value, canaries in INVALID_RUN_IDS:
            for error_type, cases in (
                (OperationsRecordError, direct),
                (ReadPageError, page),
            ):
                for case in cases:
                    with self.subTest(
                        invalid=type(value).__name__,
                        case=case.__code__.co_firstlineno,
                    ):
                        connection = _Connection()
                        self.assert_bounded(
                            error_type,
                            lambda case=case, value=value: case(
                                PostgresExecutionStore(connection), value
                            ),
                            *canaries,
                        )
                        self.assertEqual(connection.calls, [])

        for value in ("a", "r" * 200):
            for case in direct + page:
                with self.subTest(valid=value[:8], case=case.__code__.co_firstlineno):
                    driver_error = psycopg.OperationalError("driver-canary")
                    with self.assertRaises(psycopg.OperationalError) as captured:
                        case(PostgresExecutionStore(_Connection(driver_error)), value)
                    self.assertIs(captured.exception, driver_error)

    def test_claimability_and_existing_run_reject_before_factory(self):
        for reread in (
            _request(ExecutionRequestStatus.CANCELLED),
            _request(ExecutionRequestStatus.CLAIMED),
        ):
            trace = _Trace(reread=reread)
            with self.assertRaises(RunLifecycleConflict):
                self.service(trace).execute(_claim_command())
            self.assertEqual(trace.factory_calls, 0)
            self.assertNotIn("runs_for_request", trace.log)
            self.assertNotIn("claim_request", trace.log)

        trace = _Trace(runs=(_run("run-existing"),))
        with self.assertRaises(RunLifecycleConflict):
            self.service(trace).execute(_claim_command())
        self.assertEqual(trace.factory_calls, 0)
        self.assertNotIn("claim_request", trace.log)

    def test_first_factory_run_candidate_shares_full_matrix_before_mutation(self):
        for value, canaries in INVALID_RUN_IDS:
            trace = _Trace(ids=(value, "event-a", "action-a"))
            with self.subTest(invalid=type(value).__name__):
                self.assert_bounded(
                    RunLifecycleError,
                    lambda: self.service(trace).execute(_claim_command()),
                    *canaries,
                )
                self.assertEqual(trace.factory_calls, 1)
                self.assertEqual(trace.log[6:8], ["runs_for_request", "id_factory:1"])
                for mutation in (
                    "claim_request",
                    "add_run",
                    "add_event",
                    "add_action",
                    "commit",
                ):
                    self.assertNotIn(mutation, trace.log)

        for value in ("a", "r" * 200):
            trace = _Trace(ids=(value, "event-a", "action-a"))
            self.assertEqual(
                self.service(trace).execute(_claim_command()).run.run_id,
                value,
            )

    def test_raw_factory_exception_precedes_every_mutation(self):
        raw = RuntimeError("factory-operational-canary")
        trace = _Trace(factory_error=raw)
        with self.assertRaises(RuntimeError) as captured:
            self.service(trace).execute(_claim_command())
        self.assertIs(captured.exception, raw)
        for mutation in ("claim_request", "add_run", "add_event", "add_action", "commit"):
            self.assertNotIn(mutation, trace.log)

    def test_valid_claim_trace_distinguishes_all_three_factory_calls(self):
        trace = _Trace()
        self.service(trace).execute(_claim_command())
        self.assertEqual(
            trace.log,
            [
                "uow_enter",
                "get_request",
                "lock_action_idempotency",
                "action_for_idempotency",
                "get_session_for_update",
                "get_request",
                "runs_for_request",
                "id_factory:1",
                "claim_request",
                "add_run",
                "id_factory:2",
                "next_event_ordinal",
                "add_event",
                "id_factory:3",
                "next_action_ordinal",
                "add_action",
                "commit",
                "uow_exit",
            ],
        )

    def test_replay_action_run_admission_shares_full_matrix_before_selector(self):
        command = _claim_command()
        for value, canaries in INVALID_RUN_IDS:
            trace = _Trace(
                action=_replay_action(command, run_id=value),
                run=_run(),
                event=_event(),
            )
            with self.subTest(invalid=type(value).__name__):
                self.assert_bounded(
                    RunLifecycleError,
                    lambda: self.service(trace).execute(command),
                    *canaries,
                )
                self.assertEqual(trace.factory_calls, 0)
                self.assertNotIn("get_run", trace.log)

        for value in ("a", "r" * 200):
            trace = _Trace(
                action=_replay_action(command, run_id=value),
                run=_run(value),
                event=_event(value),
            )
            replay = self.service(trace).execute(command)
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.run.run_id, value)
            self.assertEqual(trace.factory_calls, 0)

    def test_replay_requires_request_run_and_run_event_congruence(self):
        command = _claim_command()
        for run, event in (
            (_run(request_id="request-other"), _event()),
            (_run(), _event("run-other")),
        ):
            trace = _Trace(action=_replay_action(command), run=run, event=event)
            self.assert_bounded(
                RunLifecycleError,
                lambda trace=trace: self.service(trace).execute(command),
            )
            self.assertEqual(trace.factory_calls, 0)

    def test_missing_replay_evidence_clears_store_error_chain(self):
        command = _claim_command()
        trace = _Trace(action=_replay_action(command), missing_replay=True)
        self.assert_bounded(
            RunLifecycleError,
            lambda: self.service(trace).execute(command),
            "run-secret-canary",
            "event-secret-canary",
        )
        self.assertEqual(trace.factory_calls, 0)

    def test_existing_run_lookup_translations_clear_store_error_chain(self):
        command = StartActivityRun(
            "run-a",
            _authority(),
            IdempotencyKey("start-a"),
        )
        cases = (
            (
                _Trace(run=_run(), missing_locator_run=True),
                "locator-run-canary",
                "get_run",
            ),
            (
                _Trace(run=_run(), missing_locked_run=True),
                "locked-run-canary",
                "get_run_for_update",
            ),
        )
        for trace, canary, final_call in cases:
            with self.subTest(final_call=final_call):
                self.assert_bounded(
                    RunLifecycleNotFound,
                    lambda trace=trace: self.service(trace).execute(command),
                    canary,
                )
                self.assertEqual(trace.log[-1], "uow_exit")
                self.assertEqual(trace.log[-2], final_call)
