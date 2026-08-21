from __future__ import annotations

from dataclasses import fields, replace
import inspect
import unittest
from unittest import mock

from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    NewlyFolded,
)
from control_plane_kit_operations.postgres import (
    runtime_authority_store as runtime_authority_store_module,
)
from control_plane_kit_operations.postgres.effect_attempt_intent_store import (
    EffectAttemptIntentStore,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
)
from tests.postgres_effect_attempt_fold_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
)
from tests.postgres_guarded_observed_effect_fold_fixture import (
    PostgresGuardedObservedEffectFoldFixture,
)


_LOOKUP_ERROR = "runtime authority lookup is invalid"
_ROW_ERROR = "registered runtime authority row is invalid"


def _forge_exact(candidate, **changes):
    forged = object.__new__(type(candidate))
    for item in fields(candidate):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(candidate, item.name)),
        )
    return forged


def _forge_reference(reference_id):
    reference = object.__new__(RuntimeAuthorityReference)
    object.__setattr__(reference, "reference_id", reference_id)
    return reference


class _HostileText(str):
    def __new__(cls, value, ledger):
        candidate = str.__new__(cls, value)
        object.__setattr__(candidate, "_ledger", ledger)
        return candidate

    def __getattribute__(self, name):
        ledger = object.__getattribute__(self, "_ledger")
        ledger.append(("attribute", name))
        raise AssertionError("hostile reference text dispatched")

    def __eq__(self, _other):
        ledger = object.__getattribute__(self, "_ledger")
        ledger.append(("equal",))
        raise AssertionError("hostile reference text compared")

    def __hash__(self):
        ledger = object.__getattribute__(self, "_ledger")
        ledger.append(("hash",))
        raise AssertionError("hostile reference text hashed")

    def __iter__(self):
        ledger = object.__getattribute__(self, "_ledger")
        ledger.append(("iterate",))
        raise AssertionError("hostile reference text iterated")

    def __len__(self):
        ledger = object.__getattribute__(self, "_ledger")
        ledger.append(("length",))
        raise AssertionError("hostile reference text measured")


def _predecessor_active_selector(store, workspace_id, authority_ref):
    invalid = (
        type(workspace_id) is not str
        or not workspace_id
        or len(workspace_id) > 512
        or any(ord(character) < 32 for character in workspace_id)
        or type(authority_ref) is not RuntimeAuthorityReference
    )
    admitted_reference = None
    if not invalid:
        reference_id = authority_ref.reference_id
        invalid = type(reference_id) is not str
    if not invalid:
        try:
            admitted_reference = RuntimeAuthorityReference(reference_id)
        except ValueError:
            invalid = True
    if invalid:
        raise RuntimeAuthorityRegistrationError(_LOOKUP_ERROR) from None
    rows = store._connection.execute(
        """
        SELECT
          registration_id,
          workspace_id,
          authority_ref,
          runtime_kind,
          authority,
          admitted_by,
          admitted_at,
          status,
          metadata
        FROM cpk_runtime_authorities
        WHERE workspace_id = %s
          AND authority_ref = %s
          AND status = 'active'
        FOR UPDATE
        """,
        (workspace_id, admitted_reference.reference_id),
    ).fetchall()
    if not rows:
        raise RuntimeAuthorityNotFound(
            "registered runtime authority was not found"
        ) from None
    if len(rows) != 1:
        raise RuntimeAuthorityRegistrationError(_ROW_ERROR) from None
    invalid = False
    try:
        candidate = runtime_authority_store_module._row_to_authority(rows[0])
    except ValueError:
        invalid = True
    if invalid:
        raise RuntimeAuthorityRegistrationError(_ROW_ERROR) from None
    return candidate


class _RowsConnection:
    def __init__(self, rows=(), *, error=None):
        self.rows = rows
        self.error = error
        self.calls = []

    def execute(self, query, parameters):
        self.calls.append(("execute", query, parameters))
        if self.error is not None:
            raise self.error
        return self

    def fetchall(self):
        self.calls.append(("fetchall",))
        return self.rows


def _assert_selector_ledger(
    test,
    connection,
    workspace_id,
    authority_ref,
    *,
    fetched=True,
):
    expected_actions = ("execute", "fetchall") if fetched else ("execute",)
    test.assertEqual(tuple(call[0] for call in connection.calls), expected_actions)
    _action, query, parameters = connection.calls[0]
    normalized = " ".join(query.split())
    test.assertTrue(normalized.startswith("SELECT "))
    test.assertIn("WHERE workspace_id = %s", normalized)
    test.assertIn("authority_ref = %s", normalized)
    test.assertIn("status = 'active'", normalized)
    test.assertIn("FOR UPDATE", normalized)
    mutation_free = normalized.replace("FOR UPDATE", "")
    test.assertNotRegex(
        mutation_free,
        r"\b(?:LIMIT|OFFSET|INSERT|UPDATE|DELETE)\b",
    )
    test.assertEqual(
        parameters,
        (workspace_id, authority_ref.reference_id),
    )


class PostgresGuardedObservedEffectFoldAuthorityTests(
    PostgresGuardedObservedEffectFoldFixture,
    unittest.TestCase,
):
    def test_control_runtime_authority_register_and_get_is_total(self) -> None:
        story = self.observed_story()
        current, intent, _record = self.seed_guarded_source(story)
        for remote in (False, True):
            with self.subTest(remote=remote):
                if remote:
                    self.connection.execute(
                        "UPDATE cpk_runtime_authorities SET status='revoked' "
                        "WHERE workspace_id=%s AND authority_ref=%s",
                        (intent.source.workspace_id, intent.authority_ref.reference_id),
                    )
                expected = self.register_runtime_authority(intent, remote=remote)
                with self.unit_of_work() as unit_of_work:
                    observed = unit_of_work.stores.runtime_authorities.get(
                        intent.source.workspace_id,
                        intent.authority_ref,
                    )
                self.assertEqual(observed, expected)
                self.assertEqual(current.state.identity, self.current_attempt().state.identity)

    def test_fresh_intent_reload_revalidates_complete_original_event_truth(self) -> None:
        story = self.observed_story()
        for fault in ("missing", "malformed", "foreign", "drifted", "original-event"):
            with self.subTest(fault=fault):
                current, intent, record = self.seed_guarded_source(story)
                side_effect = None
                observed = record
                if fault == "missing":
                    side_effect = KeyError("missing-intent-canary")
                elif fault == "malformed":
                    side_effect = OperationsRecordError("malformed-intent-canary")
                elif fault == "foreign":
                    observed = _forge_exact(
                        record,
                        identity=self.identity(activity_id="foreign"),
                    )
                elif fault == "drifted":
                    observed = self.intent_record(
                        current,
                        intent=replace(intent, authority_ref=None, authority_deliveries=()),
                    )
                else:
                    observed = replace(
                        record,
                        original_start_event=replace(
                            record.original_start_event,
                            event_id="foreign-original-event",
                        ),
                    )
                with mock.patch.object(
                    EffectAttemptIntentStore,
                    "get",
                    side_effect=side_effect,
                    return_value=observed,
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service("must-not-allocate").execute_observed(
                            self.guarded_observed_command(
                                story,
                                current=current,
                                intent=intent,
                                intent_record=record,
                            )
                        )
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)

    def test_persisted_key_no_ref_and_active_selector_authority_are_exact(self) -> None:
        target = getattr(RuntimeAuthorityStore, "get_active_for_update", None)
        selector = target or _predecessor_active_selector
        if target is not None:
            source = inspect.getsource(target)
            normalized = " ".join(source.split())
            self.assertIn("status = 'active'", normalized)
            self.assertIn("FOR UPDATE", normalized)
            self.assertIn(".fetchall()", normalized)
            self.assertNotIn("LIMIT", normalized)
            self.assertNotIn("OFFSET", normalized)
            self.assertNotRegex(
                normalized.replace("FOR UPDATE", ""),
                r"\b(?:UPDATE|DELETE|INSERT)\b",
            )
        story = self.observed_story()
        current, intent, _record = self.seed_guarded_source(story)
        expected = self.register_runtime_authority(intent)
        row = self.connection.execute(
            "SELECT registration_id, workspace_id, authority_ref, runtime_kind, "
            "authority, admitted_by, admitted_at, status, metadata "
            "FROM cpk_runtime_authorities WHERE registration_id=%s",
            (expected.registration_id,),
        ).fetchone()
        one = _RowsConnection((row,))
        selected = selector(
            RuntimeAuthorityStore(one),
            intent.source.workspace_id,
            intent.authority_ref,
        )
        self.assertEqual(selected, expected)
        _assert_selector_ledger(
            self,
            one,
            intent.source.workspace_id,
            intent.authority_ref,
        )
        self.assertEqual(current.state.identity, self.current_attempt().state.identity)

        bounded_workspace = "w" * 512
        bounded_row = list(row)
        bounded_row[1] = bounded_workspace
        bounded = _RowsConnection((tuple(bounded_row),))
        selected = selector(
            RuntimeAuthorityStore(bounded),
            bounded_workspace,
            intent.authority_ref,
        )
        self.assertEqual(selected.workspace_id, bounded_workspace)
        _assert_selector_ledger(
            self,
            bounded,
            bounded_workspace,
            intent.authority_ref,
        )

        hostile_ledger = []
        hostile_reference = _forge_reference(
            _HostileText("runtime-authority-a", hostile_ledger)
        )
        invalid_rows = (
            ("workspace-type", object(), intent.authority_ref),
            ("workspace-empty", "", intent.authority_ref),
            ("workspace-over", "w" * 513, intent.authority_ref),
            ("workspace-control", "workspace\x00a", intent.authority_ref),
            ("reference-type", intent.source.workspace_id, object()),
            (
                "reference-malformed",
                intent.source.workspace_id,
                _forge_reference(""),
            ),
            (
                "reference-hostile",
                intent.source.workspace_id,
                hostile_reference,
            ),
        )
        for label, workspace_id, authority_ref in invalid_rows:
            with self.subTest(invalid=label):
                connection = _RowsConnection((row,))
                with self.assertRaises(RuntimeAuthorityRegistrationError) as caught:
                    selector(
                        RuntimeAuthorityStore(connection),
                        workspace_id,
                        authority_ref,
                    )
                self.assertTrue(
                    str(caught.exception) == _LOOKUP_ERROR,
                    "selector escaped the fixed input error",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(connection.calls, [])
        self.assertEqual(hostile_ledger, [])

        malformed = list(row)
        malformed[3] = RuntimeKind.EXTERNAL.value
        for label, rows, category, message in (
            (
                "missing",
                (),
                RuntimeAuthorityNotFound,
                "registered runtime authority was not found",
            ),
            ("duplicate", (row, row), RuntimeAuthorityRegistrationError, _ROW_ERROR),
            (
                "malformed",
                (tuple(malformed),),
                RuntimeAuthorityRegistrationError,
                _ROW_ERROR,
            ),
        ):
            with self.subTest(selector=label):
                connection = _RowsConnection(rows)
                with self.assertRaises(category) as caught:
                    selector(
                        RuntimeAuthorityStore(connection),
                        intent.source.workspace_id,
                        intent.authority_ref,
                    )
                self.assertTrue(
                    str(caught.exception) == message,
                    "selector escaped the fixed row error",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                _assert_selector_ledger(
                    self,
                    connection,
                    intent.source.workspace_id,
                    intent.authority_ref,
                )

        for label, rows in (
            ("missing-field", (row[:-1],)),
            ("extra-field", ((*row, "foreign-extra-field"),)),
        ):
            with self.subTest(selector=label):
                connection = _RowsConnection(rows)
                observed_error = None
                try:
                    selector(
                        RuntimeAuthorityStore(connection),
                        intent.source.workspace_id,
                        intent.authority_ref,
                    )
                except Exception as error:
                    observed_error = error
                if observed_error is None:
                    self.fail("malformed selector row was accepted")
                self.assertIs(
                    type(observed_error),
                    RuntimeAuthorityRegistrationError,
                    "malformed selector row escaped the fixed category",
                )
                self.assertTrue(
                    str(observed_error) == _ROW_ERROR,
                    "malformed selector row escaped the fixed message",
                )
                self.assertIsNone(observed_error.__cause__)
                self.assertIsNone(observed_error.__context__)
                _assert_selector_ledger(
                    self,
                    connection,
                    intent.source.workspace_id,
                    intent.authority_ref,
                )

        for error_type in (TypeError, RuntimeError):
            with self.subTest(selector_raw=error_type.__name__):
                error = error_type("raw-active-selector-canary")
                connection = _RowsConnection(error=error)
                with self.assertRaises(error_type) as caught:
                    selector(
                        RuntimeAuthorityStore(connection),
                        intent.source.workspace_id,
                        intent.authority_ref,
                    )
                self.assertIs(caught.exception, error)
                _assert_selector_ledger(
                    self,
                    connection,
                    intent.source.workspace_id,
                    intent.authority_ref,
                    fetched=False,
                )

        for mode in ("none", "local", "remote"):
            with self.subTest(mode=mode):
                current, intent, record = self.seed_guarded_source(
                    story,
                    authority_ref=mode != "none",
                )
                authority = (
                    None
                    if mode == "none"
                    else self.register_runtime_authority(intent, remote=mode == "remote")
                )
                calls = []
                original = target or _predecessor_active_selector

                def selected(store, workspace_id, authority_ref):
                    calls.append((workspace_id, authority_ref))
                    return original(store, workspace_id, authority_ref)

                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    selected,
                    create=True,
                ):
                    result = self.fold_service(f"authority-{mode}").execute_observed(
                        self.guarded_observed_command(
                            story,
                            current=current,
                            intent=intent,
                            intent_record=record,
                            runtime_authority=authority,
                            register=False,
                        )
                    )
                self.assertIsInstance(result, NewlyFolded)
                self.assertEqual(
                    calls,
                    []
                    if mode == "none"
                    else [(intent.source.workspace_id, intent.authority_ref)],
                )
        self.assertIsNotNone(
            target,
            "runtime authority locking selector is missing",
        )

    def test_authority_denied_malformed_and_raw_fault_categories_are_closed(self) -> None:
        story = self.observed_story()
        for fault, category, message in (
            ("missing", EffectAttemptFoldDenied, AUTHORITY_ERROR),
            ("revoked", EffectAttemptFoldDenied, AUTHORITY_ERROR),
            ("replaced", EffectAttemptFoldDenied, AUTHORITY_ERROR),
            ("foreign", EffectAttemptFoldDenied, AUTHORITY_ERROR),
            ("malformed-kind", EffectAttemptFoldConflict, INVALID_TRUTH_ERROR),
            ("malformed-status", EffectAttemptFoldConflict, INVALID_TRUTH_ERROR),
        ):
            with self.subTest(fault=fault):
                current, intent, record = self.seed_guarded_source(story)
                accepted = self.register_runtime_authority(intent)
                candidate = accepted
                side_effect = None
                if fault in {"missing", "revoked"}:
                    side_effect = RuntimeAuthorityNotFound(
                        "registered runtime authority was not found"
                    )
                elif fault == "replaced":
                    candidate = replace(
                        accepted,
                        registration_id="runtime-authority-replaced",
                    )
                elif fault == "foreign":
                    candidate = replace(accepted, workspace_id="workspace-foreign")
                elif fault == "malformed-kind":
                    candidate = object.__new__(RegisteredRuntimeAuthority)
                    for item in fields(accepted):
                        object.__setattr__(
                            candidate,
                            item.name,
                            getattr(accepted, item.name),
                        )
                    object.__setattr__(candidate, "runtime_kind", RuntimeKind.EXTERNAL)
                else:
                    candidate = replace(
                        accepted,
                        status=RegisteredRuntimeAuthorityStatus.REVOKED,
                    )
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=side_effect,
                    return_value=candidate,
                    create=True,
                ):
                    with self.assertRaises(category) as caught:
                        self.fold_service("must-not-allocate").execute_observed(
                            self.guarded_observed_command(
                                story,
                                current=current,
                                intent=intent,
                                intent_record=record,
                                runtime_authority=accepted,
                                register=False,
                            )
                        )
                self.assertEqual(str(caught.exception), message)

        for error_type in (TypeError, RuntimeError):
            with self.subTest(raw=error_type.__name__):
                current, intent, record = self.seed_guarded_source(story)
                accepted = self.register_runtime_authority(intent)
                error = error_type("raw-active-authority-canary")
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=error,
                    create=True,
                ):
                    with self.assertRaises(error_type) as caught:
                        self.fold_service("must-not-allocate").execute_observed(
                            self.guarded_observed_command(
                                story,
                                current=current,
                                intent=intent,
                                intent_record=record,
                                runtime_authority=accepted,
                                register=False,
                            )
                        )
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
