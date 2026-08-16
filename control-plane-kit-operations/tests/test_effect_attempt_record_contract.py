from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RunId,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    OperationsRecordError,
)


EFFECT_ATTEMPTS_MODULE = "control_plane_kit_operations.effect_attempts"
REQUEST_FINGERPRINT = "a" * 64
OUTCOME_FINGERPRINT = "b" * 64
UNCERTAIN_FINGERPRINT = "c" * 64


def _load_effect_attempts_module(import_module=importlib.import_module):
    try:
        return import_module(EFFECT_ATTEMPTS_MODULE)
    except ModuleNotFoundError as error:
        if error.name != EFFECT_ATTEMPTS_MODULE:
            raise
        return None


effect_attempts_module = _load_effect_attempts_module()
EffectAttemptEventEvidence = getattr(
    effect_attempts_module,
    "EffectAttemptEventEvidence",
    None,
)
EffectAttemptRecord = getattr(effect_attempts_module, "EffectAttemptRecord", None)
effect_attempt_state_fingerprint = getattr(
    effect_attempts_module,
    "effect_attempt_state_fingerprint",
    None,
)


class HostileEffectAttemptState(EffectAttemptState):
    pass


class HostileActivityEventRecord(ActivityEventRecord):
    pass


class HostileBoundedEvidence(BoundedEvidence):
    pass


class EffectAttemptRecordContractTests(unittest.TestCase):
    maxDiff = None

    def require_language(self) -> None:
        required = {
            "EffectAttemptEventEvidence": EffectAttemptEventEvidence,
            "EffectAttemptRecord": EffectAttemptRecord,
            "effect_attempt_state_fingerprint": effect_attempt_state_fingerprint,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-attempt Operations record language is missing",
        )

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 256)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def identity(self, *, attempt: int = 1) -> EffectAttemptIdentity:
        return EffectAttemptIdentity(RunId("run-a"), "activity-a", attempt)

    def state(
        self,
        story: str = "started",
        *,
        attempt: int = 1,
    ) -> EffectAttemptState:
        identity = self.identity(attempt=attempt)
        prior_attempt = self.identity(attempt=attempt - 1) if attempt > 1 else None
        status = {
            "started": EffectAttemptStatus.STARTED,
            "succeeded": EffectAttemptStatus.SUCCEEDED,
            "failed": EffectAttemptStatus.FAILED,
            "unsupported": EffectAttemptStatus.UNSUPPORTED,
            "uncertain": EffectAttemptStatus.UNCERTAIN,
            "recovered-succeeded": EffectAttemptStatus.SUCCEEDED,
            "recovered-failed": EffectAttemptStatus.FAILED,
            "abandoned": EffectAttemptStatus.ABANDONED,
        }[story]
        recovery = None
        if story.startswith("recovered-") or story == "abandoned":
            resolution = {
                "recovered-succeeded": EffectRecoveryResolution.SUCCEEDED,
                "recovered-failed": EffectRecoveryResolution.FAILED,
                "abandoned": EffectRecoveryResolution.ABANDONED,
            }[story]
            recovery = EffectRecoveryDecision(
                "decision-a",
                identity,
                resolution,
                UNCERTAIN_FINGERPRINT,
                OUTCOME_FINGERPRINT,
            )
        outcome = None if story == "started" else OUTCOME_FINGERPRINT
        if story == "uncertain":
            outcome = UNCERTAIN_FINGERPRINT
        return EffectAttemptState(
            identity=identity,
            request_fingerprint=REQUEST_FINGERPRINT,
            fence=EffectAttemptFence("worker-a", 7),
            status=status,
            outcome_fingerprint=outcome,
            prior_attempt=prior_attempt,
            recovery_decision=recovery,
        )

    def started_state(self, state: EffectAttemptState) -> EffectAttemptState:
        return EffectAttemptState(
            identity=state.identity,
            request_fingerprint=state.request_fingerprint,
            fence=state.fence,
            status=EffectAttemptStatus.STARTED,
            prior_attempt=state.prior_attempt,
        )

    def evidence_for(self, state: EffectAttemptState) -> BoundedEvidence:
        self.require_language()
        evidence = EffectAttemptEventEvidence(
            state.identity.attempt,
            effect_attempt_state_fingerprint(state),
        )
        return BoundedEvidence.from_mapping(
            {"effect_attempt": evidence.descriptor()}
        )

    def event_kind(
        self,
        story: str,
        *,
        compensation: bool,
    ) -> ActivityEventKind:
        names = {
            "started": "STEP_COMPENSATION_STARTED"
            if compensation
            else "STEP_STARTED",
            "succeeded": "STEP_COMPENSATION_SUCCEEDED"
            if compensation
            else "STEP_SUCCEEDED",
            "failed": "STEP_COMPENSATION_FAILED"
            if compensation
            else "STEP_FAILED",
            "unsupported": "STEP_COMPENSATION_UNSUPPORTED"
            if compensation
            else "STEP_UNSUPPORTED",
            "uncertain": "STEP_COMPENSATION_UNCERTAIN"
            if compensation
            else "STEP_UNCERTAIN",
            "recovered-succeeded": (
                "STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED"
                if compensation
                else "STEP_UNCERTAINTY_RESOLVED_SUCCEEDED"
            ),
            "recovered-failed": (
                "STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED"
                if compensation
                else "STEP_UNCERTAINTY_RESOLVED_FAILED"
            ),
            "abandoned": "STEP_COMPENSATION_UNCERTAINTY_ABANDONED"
            if compensation
            else "STEP_UNCERTAINTY_ABANDONED",
        }
        return getattr(ActivityEventKind, names[story])

    def event(
        self,
        state: EffectAttemptState,
        kind: ActivityEventKind,
        *,
        event_id: str,
        ordinal: int,
        occurred_at: str,
        evidence: BoundedEvidence | None = None,
        run_id: str | None = None,
        activity_id: str | None = None,
    ) -> ActivityEventRecord:
        return ActivityEventRecord(
            event_id=event_id,
            run_id=run_id or state.identity.run_id.value,
            ordinal=ordinal,
            kind=kind,
            occurred_at=occurred_at,
            activity_id=activity_id or state.identity.activity_id,
            evidence=evidence or self.evidence_for(state),
        )

    def record(
        self,
        story: str = "started",
        *,
        compensation: bool = False,
        attempt: int = 1,
        original_time: str = "2030-01-01T00:00:02.000000Z",
        latest_time: str = "2030-01-01T00:00:01.000000Z",
    ):
        self.require_language()
        state = self.state(story, attempt=attempt)
        original = self.event(
            self.started_state(state),
            self.event_kind("started", compensation=compensation),
            event_id="event-start",
            ordinal=3,
            occurred_at=original_time,
        )
        latest = original
        if story != "started":
            latest = self.event(
                state,
                self.event_kind(story, compensation=compensation),
                event_id="event-latest",
                ordinal=7,
                occurred_at=latest_time,
            )
        return EffectAttemptRecord(state, original, latest)

    def test_public_shape_is_frozen_exact_and_root_identical(self) -> None:
        self.require_language()
        self.assertIs(
            operations_root.EffectAttemptEventEvidence,
            EffectAttemptEventEvidence,
        )
        self.assertIs(operations_root.EffectAttemptRecord, EffectAttemptRecord)
        self.assertIs(
            operations_root.effect_attempt_state_fingerprint,
            effect_attempt_state_fingerprint,
        )
        self.assertTrue(dataclasses.is_dataclass(EffectAttemptEventEvidence))
        self.assertTrue(dataclasses.is_dataclass(EffectAttemptRecord))
        self.assertTrue(EffectAttemptEventEvidence.__dataclass_params__.frozen)
        self.assertTrue(EffectAttemptRecord.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(EffectAttemptEventEvidence)
            ),
            ("attempt", "state_fingerprint"),
        )
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(EffectAttemptRecord)),
            ("state", "original_start_event", "latest_transition_event"),
        )

    def test_state_fingerprint_has_one_canonical_golden_representation(self) -> None:
        self.require_language()
        started = self.state()
        self.assertEqual(
            effect_attempt_state_fingerprint(started),
            "36b2e0735976aab98be59df725ded5332e5709dbb7f60c7244f5a8a3c1f86416",
        )
        descriptor = started.descriptor()
        canonical = json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(
            canonical,
            b'{"fence":{"generation":7,"worker_id":"worker-a"},'
            b'"identity":{"activity_id":"activity-a","attempt":1,'
            b'"run_id":"run-a"},"outcome_fingerprint":null,'
            b'"prior_attempt":null,"recovery_decision":null,'
            b'"request_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":"started"}',
        )

    def test_event_evidence_is_exact_bounded_and_descriptor_stable(self) -> None:
        self.require_language()
        fingerprint = effect_attempt_state_fingerprint(self.state())
        evidence = EffectAttemptEventEvidence(1, fingerprint)
        self.assertEqual(
            evidence.descriptor(),
            {"attempt": 1, "state_fingerprint": fingerprint},
        )
        self.assertEqual(
            BoundedEvidence.from_mapping(
                {"effect_attempt": evidence.descriptor()}
            ).descriptor(),
            {
                "effect_attempt": {
                    "attempt": 1,
                    "state_fingerprint": fingerprint,
                }
            },
        )
        for changes in (
            {"attempt": True},
            {"attempt": 0},
            {"attempt": 2_147_483_648},
            {"state_fingerprint": "A" * 64},
            {"state_fingerprint": "secret-canary"},
        ):
            with self.subTest(changes=changes):
                values = {"attempt": 1, "state_fingerprint": fingerprint}
                values.update(changes)
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptEventEvidence(**values)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt event evidence is invalid",
                )
                self.assert_safe_error(caught.exception, "secret-canary")

    def test_every_state_story_is_valid_in_both_event_phases(self) -> None:
        self.require_language()
        stories = (
            "started",
            "succeeded",
            "failed",
            "unsupported",
            "uncertain",
            "recovered-succeeded",
            "recovered-failed",
            "abandoned",
        )
        for compensation in (False, True):
            for story in stories:
                with self.subTest(compensation=compensation, story=story):
                    record = self.record(story, compensation=compensation)
                    self.assertEqual(record.state, self.state(story))
                    self.assertEqual(
                        record.original_start_event.kind,
                        self.event_kind("started", compensation=compensation),
                    )
                    self.assertEqual(
                        record.latest_transition_event.kind,
                        self.event_kind(story, compensation=compensation),
                    )

    def test_retry_lineage_and_nonchronological_timestamps_are_preserved(self) -> None:
        self.require_language()
        reverse_time = self.record(
            "succeeded",
            attempt=2,
            original_time="2030-01-01T00:00:09.000000Z",
            latest_time="2030-01-01T00:00:01.000000Z",
        )
        equal_time = self.record(
            "failed",
            original_time="2030-01-01T00:00:05.000000Z",
            latest_time="2030-01-01T00:00:05.000000Z",
        )
        self.assertEqual(reverse_time.state.identity.attempt, 2)
        self.assertEqual(reverse_time.state.prior_attempt.attempt, 1)
        self.assertGreater(
            reverse_time.latest_transition_event.ordinal,
            reverse_time.original_start_event.ordinal,
        )
        self.assertEqual(
            equal_time.latest_transition_event.occurred_at,
            equal_time.original_start_event.occurred_at,
        )

    def test_exact_nominal_state_event_and_evidence_types_are_required(self) -> None:
        self.require_language()
        state = self.state()
        original = self.record().original_start_event
        hostile_state = HostileEffectAttemptState(**state.__dict__)
        hostile_event = HostileActivityEventRecord(**original.__dict__)
        hostile_evidence = HostileBoundedEvidence(original.evidence.canonical_json)
        hostile_evidence_event = dataclasses.replace(
            original,
            evidence=hostile_evidence,
        )
        for values in (
            (hostile_state, original, original),
            (state, hostile_event, hostile_event),
            (state, hostile_evidence_event, hostile_evidence_event),
        ):
            with self.subTest(values=tuple(type(value).__name__ for value in values)):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(*values)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )
                self.assert_safe_error(caught.exception)
        with self.assertRaises(OperationsRecordError) as caught:
            effect_attempt_state_fingerprint(hostile_state)
        self.assertEqual(
            str(caught.exception),
            "effect attempt state must be typed",
        )
        self.assert_safe_error(caught.exception)

    def test_event_run_activity_attempt_and_fingerprint_must_match(self) -> None:
        self.require_language()
        state = self.state("succeeded")
        valid = self.record("succeeded")
        wrong_attempt = BoundedEvidence.from_mapping(
            {
                "effect_attempt": {
                    "attempt": 2,
                    "state_fingerprint": effect_attempt_state_fingerprint(state),
                }
            }
        )
        wrong_fingerprint = BoundedEvidence.from_mapping(
            {
                "effect_attempt": {
                    "attempt": 1,
                    "state_fingerprint": "d" * 64,
                }
            }
        )
        extra_evidence = BoundedEvidence.from_mapping(
            {
                "effect_attempt": {
                    "attempt": 1,
                    "state_fingerprint": effect_attempt_state_fingerprint(state),
                    "secret-canary": "must-not-render",
                }
            }
        )
        candidates = (
            dataclasses.replace(
                valid.latest_transition_event,
                run_id="run-foreign-canary",
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                activity_id="activity-foreign-canary",
            ),
            dataclasses.replace(valid.latest_transition_event, evidence=wrong_attempt),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=wrong_fingerprint,
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=extra_evidence,
            ),
        )
        for latest in candidates:
            with self.subTest(latest=latest):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(
                        state,
                        valid.original_start_event,
                        latest,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )
                self.assert_safe_error(
                    caught.exception,
                    "foreign-canary",
                    "secret-canary",
                    "must-not-render",
                )

    def test_phase_and_transition_kind_are_derived_not_caller_selected(self) -> None:
        self.require_language()
        direct = self.record("succeeded")
        recovered = self.record("recovered-succeeded")
        compensation = self.record("failed", compensation=True)
        candidates = (
            (
                direct.state,
                direct.original_start_event,
                dataclasses.replace(
                    direct.latest_transition_event,
                    kind=ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
                ),
            ),
            (
                recovered.state,
                recovered.original_start_event,
                dataclasses.replace(
                    recovered.latest_transition_event,
                    kind=ActivityEventKind.STEP_SUCCEEDED,
                ),
            ),
            (
                compensation.state,
                compensation.original_start_event,
                dataclasses.replace(
                    compensation.latest_transition_event,
                    kind=ActivityEventKind.STEP_FAILED,
                ),
            ),
            (
                direct.state,
                dataclasses.replace(
                    direct.original_start_event,
                    kind=ActivityEventKind.STEP_COMPENSATION_STARTED,
                ),
                direct.latest_transition_event,
            ),
        )
        for values in candidates:
            with self.subTest(kinds=(values[1].kind, values[2].kind)):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(*values)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )
                self.assert_safe_error(caught.exception)

    def test_event_ordinal_and_started_identity_laws_are_exact(self) -> None:
        self.require_language()
        settled = self.record("succeeded")
        for ordinal in (3, 2):
            latest = dataclasses.replace(
                settled.latest_transition_event,
                ordinal=ordinal,
            )
            with self.subTest(ordinal=ordinal):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(
                        settled.state,
                        settled.original_start_event,
                        latest,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )

        started = self.record()
        for latest in (
            dataclasses.replace(
                started.latest_transition_event,
                event_id="event-latest-canary",
            ),
            dataclasses.replace(started.latest_transition_event, ordinal=4),
            dataclasses.replace(
                started.latest_transition_event,
                occurred_at="2030-01-01T00:00:03.000000Z",
            ),
        ):
            with self.subTest(latest=latest):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(
                        started.state,
                        started.original_start_event,
                        latest,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )
                self.assert_safe_error(caught.exception, "event-latest-canary")

    def test_original_event_commits_exact_started_state(self) -> None:
        self.require_language()
        record = self.record("recovered-failed")
        current_fingerprint = effect_attempt_state_fingerprint(record.state)
        invalid_originals = (
            dataclasses.replace(
                record.original_start_event,
                evidence=BoundedEvidence.from_mapping(
                    {
                        "effect_attempt": {
                            "attempt": 1,
                            "state_fingerprint": current_fingerprint,
                        }
                    }
                ),
            ),
            dataclasses.replace(
                record.original_start_event,
                evidence=BoundedEvidence.from_mapping({}),
            ),
        )
        for original in invalid_originals:
            with self.subTest(original=original):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptRecord(
                        record.state,
                        original,
                        record.latest_transition_event,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt record is invalid",
                )
                self.assert_safe_error(caught.exception)

    def test_module_is_db_free_and_has_one_exhaustive_inventory_row(self) -> None:
        self.require_language()
        source = Path(effect_attempts_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertFalse(
            [name for name in imports if "postgres" in name or "store" in name]
        )

        inventory_path = Path(
            os.environ.get(
                "CPK_PACKAGE_MODULE_INVENTORY",
                Path(__file__).parents[2]
                / "docs"
                / "architecture"
                / "package-module-inventory.json",
            )
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in inventory["modules"]
            if row["module"] == "control_plane_kit_operations.effect_attempts"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "operation")
        self.assertEqual(
            rows[0]["destination"],
            "control_plane_kit_operations.effect_attempts",
        )
        self.assertEqual(rows[0]["optional_external_dependencies"], [])
        self.assertEqual(
            rows[0]["canonical_public_exports"],
            [
                "EffectAttemptEventEvidence",
                "EffectAttemptRecord",
                "effect_attempt_state_fingerprint",
            ],
        )
        self.assertIn(
            "tests/test_effect_attempt_record_contract.py",
            rows[0]["protecting_tests"],
        )


if __name__ == "__main__":
    unittest.main()
