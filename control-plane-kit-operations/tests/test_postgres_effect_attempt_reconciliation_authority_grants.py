from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_operations import effect_attempt_reconciliation_interpreter
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderAuthorizationDenied,
    SecretProviderRegistrationError,
    SecretUseAuthorizationConflict,
    SecretUseAuthorizationService,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    FailIfFold,
    FailIfObserver,
    PostgresEffectAttemptReconciliationFixture,
    UnitOfWorkLedger,
)


def _authorization_row_projection(row):
    return (
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6].isoformat().replace("+00:00", "Z"),
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
    )


def _authorization_command_projection(candidate):
    return (
        candidate.workspace_id,
        candidate.reference.reference_id,
        candidate.intent.value,
        candidate.actor_subject,
        candidate.correlation_id,
        candidate.requested_at,
        candidate.operation_id,
        candidate.session_id,
        candidate.run_id,
        candidate.activity_id,
        candidate.effect_id,
    )


class PostgresEffectAttemptReconciliationAuthorityGrantTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    def test_control_active_authority_and_secret_authorization_are_total(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.assertGreater(len(uses), 0)
        self.assertEqual(tuple(sorted(set(uses), key=lambda value: (value[0].reference_id, value[1].value))), uses)
        self.admit_secret_uses(uses)
        service = SecretUseAuthorizationService(self.unit_of_work)
        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        grants = tuple(
            service.authorize_resolution(
                self.authorization_command(
                    current,
                    intent,
                    reference,
                    use_intent,
                    command=command,
                )
            )
            for reference, use_intent in uses
        )
        replayed = tuple(
            service.authorize_resolution(
                self.authorization_command(
                    current,
                    intent,
                    reference,
                    use_intent,
                    command=command,
                    requested_at="2030-01-01T00:00:09Z",
                )
            )
            for reference, use_intent in uses
        )
        self.assertEqual(replayed, grants)
        self.assertEqual(len(self.authorization_rows()), len(uses))
        self.assertNotIn("secret-value-canary", repr(grants))

    def test_no_reference_is_none_and_zero_use_performs_no_authority_or_grant_io(self) -> None:
        story = self.observed_story()
        forbidden = AssertionError("zero-use local reconciliation crossed authority IO")
        with self.subTest(world="zero-use"):
            current, intent, _record, _authority = self.seed_reconciliation_source(
                story,
                authority_ref=False,
                zero_use=True,
            )
            self.assertEqual(self.required_secret_uses(current, intent, None), ())
            observer = self.observer_for(story, current, intent)
            fold = FailIfFold("zero-use control reached fold")
            lock_order = []
            original_request = PostgresExecutionStore.get_request_for_update
            original_run = PostgresExecutionStore.get_run_for_request_for_update
            original_attempt = EffectAttemptStore.get_for_update

            def request(store, request_id):
                lock_order.append("request")
                return original_request(store, request_id)

            def run(store, request_id, run_id):
                lock_order.append("run")
                return original_run(store, request_id, run_id)

            def attempt(store, identity):
                lock_order.append("attempt")
                return original_attempt(store, identity)

            with mock.patch.object(
                PostgresExecutionStore,
                "get_request_for_update",
                request,
            ), mock.patch.object(
                PostgresExecutionStore,
                "get_run_for_request_for_update",
                run,
            ), mock.patch.object(
                EffectAttemptStore,
                "get_for_update",
                attempt,
            ), mock.patch.object(
                RuntimeAuthorityStore,
                "get_active_for_update",
                side_effect=forbidden,
                create=True,
            ), mock.patch.object(
                SecretUseAuthorizationService,
                "authorize_resolution",
                side_effect=forbidden,
            ):
                with self.assertRaises(AssertionError) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(self.reconciliation_command(current))
            self.assertIs(caught.exception, fold.error)
            self.assertEqual(len(observer.calls), 1)
            self.assertIsNone(observer.calls[0][1])
            self.assertEqual(self.authorization_rows(), ())
            self.assertEqual(lock_order, ["request", "run", "attempt"])

        with self.subTest(secret_bearing_scope="execution-operate-only"):
            current, intent, _record, authority = self.seed_reconciliation_source(story)
            uses = self.required_secret_uses(current, intent, authority)
            self.assertGreater(len(uses), 0)
            observer = FailIfObserver("execution-only claimant reached observer")
            fold = FailIfFold("execution-only claimant reached fold")
            with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                self.reconciliation_service(observer, fold_service=fold).execute(
                    self.reconciliation_command(
                        current,
                        scopes=(PolicyScope.EXECUTION_OPERATE,),
                    )
                )
            self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(self.authorization_rows(), ())
            self.assertEqual(observer.calls, [])
            self.assertEqual(fold.calls, [])

        with self.subTest(world="no-reference-product"):
            current, intent, _record, _authority = self.seed_reconciliation_source(
                story,
                authority_ref=False,
            )
            uses = self.required_secret_uses(current, intent, None)
            self.assertGreater(len(uses), 0)
            self.admit_secret_uses(uses)
            observer = self.observer_for(story, current, intent)
            fold = FailIfFold("no-reference product control reached fold")
            with mock.patch.object(
                RuntimeAuthorityStore,
                "get_active_for_update",
                side_effect=forbidden,
                create=True,
            ):
                with self.assertRaises(AssertionError) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
            self.assertIs(caught.exception, fold.error)
            self.assertEqual(len(observer.calls), 1)
            self.assertIsNone(observer.calls[0][1])
            self.assertEqual(len(self.authorization_rows()), len(uses))

    def test_referenced_authority_is_exact_active_workspace_reference_and_kind(self) -> None:
        story = self.observed_story()
        for remote in (False, True):
            with self.subTest(lawful=remote):
                current, intent, _record, authority = self.seed_reconciliation_source(
                    story,
                    remote=remote,
                )
                calls = []

                def active(store, workspace_id, authority_ref):
                    calls.append((workspace_id, authority_ref))
                    return authority

                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    active,
                    create=True,
                ):
                    with self.assertRaises(AssertionError):
                        self.reconciliation_service(
                            FailIfObserver("lawful authority reached observer"),
                            fold_service=FailIfFold(),
                        ).execute(
                            self.reconciliation_command(
                                current,
                                scopes=(
                                    PolicyScope.EXECUTION_OPERATE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                ),
                            )
                        )
                self.assertEqual(
                    calls,
                    [(intent.source.workspace_id, intent.authority_ref)],
                )
                self.assertNotIn(authority.registration_id, repr(self.reconciliation_command(current)))

        for fault in ("missing", "revoked", "foreign-workspace", "foreign-reference"):
            with self.subTest(fault=fault):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                side_effect = None
                observed = authority
                if fault == "missing":
                    side_effect = RuntimeAuthorityNotFound("authority-canary")
                elif fault == "revoked":
                    observed = replace(authority, status=RegisteredRuntimeAuthorityStatus.REVOKED)
                elif fault == "foreign-workspace":
                    observed = replace(authority, workspace_id="workspace-foreign")
                elif fault == "foreign-reference":
                    observed = replace(
                        authority,
                        authority_ref=RuntimeAuthorityReference("authority-foreign"),
                    )
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=side_effect,
                    return_value=observed,
                    create=True,
                ):
                    with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_malformed_authority_is_conflict_and_raw_selector_faults_escape(self) -> None:
        story = self.observed_story()
        expected = RuntimeAuthorityRegistrationError("row-canary")
        for error in (expected, TypeError("raw-type-canary"), RuntimeError("raw-runtime-canary")):
            with self.subTest(error=type(error).__name__):
                current, _intent, _record, _authority = self.seed_reconciliation_source(story)
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=error,
                    create=True,
                ):
                    if error is expected:
                        with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                            self.reconciliation_service(
                                FailIfObserver(),
                                fold_service=FailIfFold(),
                            ).execute(self.reconciliation_command(current))
                        self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertIsNone(caught.exception.__context__)
                    else:
                        with self.assertRaises(type(error)) as caught:
                            self.reconciliation_service(
                                FailIfObserver(),
                                fold_service=FailIfFold(),
                            ).execute(self.reconciliation_command(current))
                        self.assertIs(caught.exception, error)

    def test_grants_are_exact_deduplicated_retry_stable_and_outside_observer(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.admit_secret_uses(uses)
        ledger = UnitOfWorkLedger(self.unit_of_work)
        observer_error = RuntimeError("observer-stop-canary")
        observer = self.observer_for(
            story,
            current,
            intent,
            ledger=ledger,
            error=observer_error,
        )
        authorization_commands = []
        original = SecretUseAuthorizationService.authorize_resolution

        def authorize(service, candidate):
            authorization_commands.append(candidate)
            return original(service, candidate)

        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:02Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            authorize,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    observer,
                    ledger=ledger,
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, observer_error)
        first = self.authorization_rows()
        self.assertEqual(len(first), len(uses))
        expected_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
            )
            for reference, use_intent in uses
        )
        self.assertEqual(tuple(authorization_commands), expected_commands)
        self.assertTrue(
            all(
                candidate.actor_scopes == command.authority.scopes
                for candidate in expected_commands
            )
        )
        self.assertEqual(
            tuple(_authorization_row_projection(row) for row in first),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in expected_commands
            ),
        )
        self.assertEqual(ledger.active, 0)
        self.assertEqual(ledger.entries, 1 + len(uses))
        self.assertEqual(ledger.entries, ledger.exits)

        later = RuntimeError("observer-retry-stop-canary")
        retry_observer = self.observer_for(
            story,
            current,
            intent,
            ledger=ledger,
            error=later,
        )
        authorization_commands.clear()
        with self.lease_observation("2030-01-01T00:00:09Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            authorize,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    retry_observer,
                    ledger=ledger,
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, later)
        expected_retry_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
                requested_at="2030-01-01T00:00:09Z",
            )
            for reference, use_intent in uses
        )
        self.assertEqual(tuple(authorization_commands), expected_retry_commands)
        self.assertEqual(self.authorization_rows(), first)
        self.assertEqual(ledger.entries, 2 * (1 + len(uses)))
        self.assertEqual(ledger.entries, ledger.exits)

    def test_partial_grant_retry_and_changed_actor_preserve_auditable_truth(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.assertGreaterEqual(len(uses), 2)
        self.admit_secret_uses(uses)
        original = SecretUseAuthorizationService.authorize_resolution
        calls = 0

        def fail_second(service, command):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SecretProviderRegistrationError("second-grant-canary")
            return original(service, command)

        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:02Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            fail_second,
        ):
            with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                self.reconciliation_service(
                    FailIfObserver(),
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        committed_prefix = self.authorization_rows()
        self.assertEqual(len(committed_prefix), 1)

        observer_error = RuntimeError("resume-after-grants-canary")
        with self.lease_observation("2030-01-01T00:00:09Z"):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    self.observer_for(story, current, intent, error=observer_error),
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, observer_error)
        first_actor_rows = self.authorization_rows()
        self.assertEqual(len(first_actor_rows), len(uses))
        self.assertEqual(first_actor_rows[:1], committed_prefix)
        same_actor_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
                requested_at=(
                    "2030-01-01T00:00:02Z"
                    if position == 0
                    else "2030-01-01T00:00:09Z"
                ),
            )
            for position, (reference, use_intent) in enumerate(uses)
        )
        self.assertEqual(
            tuple(_authorization_row_projection(row) for row in first_actor_rows),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in same_actor_commands
            ),
        )

        self.replace_current_claim(worker_id="worker-b", generation=8)
        new_actor_error = RuntimeError("new-actor-stop-canary")
        changed_actor = self.reconciliation_command(
            current,
            worker_id="worker-b",
            generation=8,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:10Z"):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    self.observer_for(story, current, intent, error=new_actor_error),
                    fold_service=FailIfFold(),
                ).execute(changed_actor)
        self.assertIs(caught.exception, new_actor_error)
        all_rows = self.authorization_rows()
        self.assertEqual(len(all_rows), 2 * len(uses))
        self.assertEqual({row[4] for row in all_rows}, {"worker-a", "worker-b"})
        self.assertEqual(
            tuple(row for row in all_rows if row[4] == "worker-a"),
            first_actor_rows,
        )
        changed_actor_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=changed_actor,
                requested_at="2030-01-01T00:00:10Z",
            )
            for reference, use_intent in uses
        )
        self.assertTrue(
            all(
                candidate.actor_scopes == changed_actor.authority.scopes
                for candidate in changed_actor_commands
            )
        )
        self.assertEqual(
            tuple(
                _authorization_row_projection(row)
                for row in all_rows
                if row[4] == "worker-b"
            ),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in changed_actor_commands
            ),
        )

    def test_expected_authorization_errors_are_fixed_and_unexpected_faults_are_raw(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(story)
        use = self.required_secret_uses(current, intent, authority)[0]
        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        for error in (
            SecretProviderAuthorizationDenied("denied-canary"),
            SecretProviderRegistrationError("malformed-canary"),
            SecretUseAuthorizationConflict("conflict-canary"),
        ):
            with self.subTest(expected=type(error).__name__):
                with mock.patch.object(
                    effect_attempt_reconciliation_interpreter,
                    "required_secret_uses_for_runtime_effect",
                    return_value=(use,),
                    create=True,
                ), mock.patch.object(
                    SecretUseAuthorizationService,
                    "authorize_resolution",
                    side_effect=error,
                ):
                    with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(command)
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        for error_type in (TypeError, RuntimeError):
            with self.subTest(raw=error_type.__name__):
                error = error_type("raw-authorizer-canary")
                with mock.patch.object(
                    effect_attempt_reconciliation_interpreter,
                    "required_secret_uses_for_runtime_effect",
                    return_value=(use,),
                    create=True,
                ), mock.patch.object(
                    SecretUseAuthorizationService,
                    "authorize_resolution",
                    side_effect=error,
                ):
                    with self.assertRaises(error_type) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(command)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
