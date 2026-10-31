from __future__ import annotations

import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.effect_attempt_fold import EffectAttemptFoldResult
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.runtime_effect_reconciliation_fixture import (
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationService,
    ReconcileEffectAttempt,
    RuntimeEffectReconciliationFixture,
)


class FailIfUnitOfWork:
    def __init__(self, message: str) -> None:
        self.error = AssertionError(message)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise self.error


class FailIfObserver:
    def __init__(self) -> None:
        self.calls = 0

    def observe(self, _request, _authority):
        self.calls += 1
        raise AssertionError("reconciliation contract invoked observer IO")


class FailIfFold:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _command):
        self.calls += 1
        raise AssertionError("reconciliation contract invoked atomic fold")


class EffectAttemptReconciliationInterpreterContractTests(
    RuntimeEffectReconciliationFixture,
    unittest.TestCase,
):
    maxDiff = None

    def service(self, unit_of_work_factory, observer=None, fold_service=None):
        self.require_service()
        return EffectAttemptReconciliationService(
            unit_of_work_factory,
            observer=observer or FailIfObserver(),
            fold_service=fold_service or FailIfFold(),
        )

    def test_invalid_and_scope_denied_commands_precede_unit_of_work(self) -> None:
        self.require_language()
        self.require_service()
        valid = self.command()

        class HostileCommand(ReconcileEffectAttempt):
            pass

        class HostileText(str):
            dispatches: list[str] = []

            def __getattribute__(self, name):
                if name == "__class__":
                    type(self).dispatches.append("__class__")
                    raise AssertionError("hostile class access dispatched")
                return str.__getattribute__(self, name)

            def __len__(self):
                self.dispatches.append("len")
                raise AssertionError("hostile length dispatched")

            def encode(self, *_args, **_kwargs):
                self.dispatches.append("encode")
                raise AssertionError("hostile encode dispatched")

        class HostileIdentity(EffectAttemptIdentity):
            pass

        def bypass(command_type=ReconcileEffectAttempt, **changes):
            values = {
                "request_id": valid.request_id,
                "identity": valid.identity,
                "authority": valid.authority,
                "fence": valid.fence,
            }
            values.update(changes)
            command = object.__new__(command_type)
            for name, value in values.items():
                object.__setattr__(command, name, value)
            return command

        def forge_exact(value_type, **values):
            value = object.__new__(value_type)
            for name, field_value in values.items():
                object.__setattr__(value, name, field_value)
            return value

        hostile_identity = HostileIdentity(
            valid.identity.run_id,
            valid.identity.activity_id,
            valid.identity.attempt,
        )
        hostile_run_id = forge_exact(
            RunId,
            value=HostileText("run-canary"),
        )
        forged_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=hostile_run_id,
            activity_id=valid.identity.activity_id,
            attempt=valid.identity.attempt,
        )
        malformed = object.__new__(ReconcileEffectAttempt)
        candidates = (
            ("raw", object()),
            ("hostile-outer", bypass(HostileCommand)),
            ("hostile-text", bypass(request_id=HostileText("request-canary"))),
            ("hostile-identity", bypass(identity=hostile_identity)),
            ("exact-forged-nested-run-id", bypass(identity=forged_identity)),
            ("missing-fields", malformed),
        )
        for label, candidate in candidates:
            with self.subTest(candidate=label):
                HostileText.dispatches.clear()
                factory = FailIfUnitOfWork("invalid command opened a unit of work")
                observer = FailIfObserver()
                fold = FailIfFold()
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(factory, observer, fold).execute(candidate)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt reconciliation command is invalid",
                )
                self.assert_safe_error(caught.exception, "canary")
                self.assertEqual(HostileText.dispatches, [])
                self.assertEqual(factory.calls, 0)
                self.assertEqual(observer.calls, 0)
                self.assertEqual(fold.calls, 0)

        denied = self.command(authority=self.authority(scopes=()))
        factory = FailIfUnitOfWork("scope denial opened a unit of work")
        observer = FailIfObserver()
        fold = FailIfFold()
        with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
            self.service(factory, observer, fold).execute(denied)
        self.assertEqual(str(caught.exception), "scope execution:operate is missing")
        self.assert_safe_error(caught.exception)
        self.assertEqual(factory.calls, 0)
        self.assertEqual(observer.calls, 0)
        self.assertEqual(fold.calls, 0)

    def test_valid_command_reaches_only_the_injected_unit_of_work_boundary(self) -> None:
        self.require_language()
        self.require_service()
        factory = FailIfUnitOfWork("valid command reached unit of work")
        observer = FailIfObserver()
        fold = FailIfFold()
        with self.assertRaises(AssertionError) as caught:
            self.service(factory, observer, fold).execute(self.command())
        self.assertIs(caught.exception, factory.error)
        self.assertEqual(factory.calls, 1)
        self.assertEqual(observer.calls, 0)
        self.assertEqual(fold.calls, 0)



if __name__ == "__main__":
    unittest.main()
