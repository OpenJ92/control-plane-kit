from __future__ import annotations

import inspect
from pathlib import Path
import unittest

import control_plane_kit_architecture_testing as architecture_testing
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.effect_attempt_fold import EffectAttemptFoldResult
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.runtime_effect_reconciliation_fixture import (
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationService,
    INTERPRETER_MODULE,
    LANGUAGE_MODULE,
    ReconcileEffectAttempt,
    RuntimeEffectReconciliationFixture,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_SOURCE_PATH = (
    "control_plane_kit_operations/effect_attempt_reconciliation.py"
)
INTERPRETER_SOURCE_PATH = (
    "control_plane_kit_operations/"
    "effect_attempt_reconciliation_interpreter.py"
)

EXACT_LANGUAGE_IMPORTS = (
    architecture_testing.ImportSurfaceEntry("__future__", "annotations", None),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptIdentity",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "RunId",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.policies",
        "PolicyScope",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservationRequest",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservationResult",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.execution_leases",
        "ExecutionLeaseFence",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.lifecycle",
        "ExecutionWorkerAuthority",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.runtime_authorities",
        "RegisteredRuntimeAuthority",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.workflows",
        "InvalidOperationCommand",
        None,
    ),
    architecture_testing.ImportSurfaceEntry("dataclasses", "dataclass", None),
    architecture_testing.ImportSurfaceEntry("typing", "Protocol", None),
)
EXACT_LANGUAGE_CALLS = (
    *(
        architecture_testing.ResolvedCallTarget("_bounded_command_text")
        for _ in range(5)
    ),
    architecture_testing.ResolvedCallTarget("_valid_reconcile_command"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.EffectAttemptIdentity"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.RunId"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.execution_leases.ExecutionLeaseFence"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.lifecycle.ExecutionWorkerAuthority"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.workflows.InvalidOperationCommand"
    ),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("len"),
    architecture_testing.ResolvedCallTarget("ord"),
    *(
        architecture_testing.ResolvedCallTarget("type")
        for _ in range(14)
    ),
    architecture_testing.ResolvedCallTarget("value.encode"),
)
EXACT_INTERPRETER_IMPORTS = tuple(
    architecture_testing.ImportSurfaceEntry(*value)
    for value in (
        ("__future__", "annotations", None),
        ("control_plane_kit_core.operations", "EffectAttemptStatus", None),
        ("control_plane_kit_core.operations.lifecycle", "ExecutionRequestStatus", None),
        ("control_plane_kit_core.policies", "PolicyScope", None),
        (
            "control_plane_kit_core.runtime_effect_observation",
            "RuntimeEffectObservationRequest",
            None,
        ),
        (
            "control_plane_kit_core.runtime_effect_observation",
            "runtime_effect_request_for_intent",
            None,
        ),
        ("control_plane_kit_core.secrets", "SecretResolutionGrant", None),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldConflict",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldDenied",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldNotFound",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldResult",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "ExistingFold",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "FoldEffectAttempt",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "GuardedObservedEffectFold",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "NewlyFolded",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_intent_evidence",
            "EffectAttemptIntentRecord",
            None,
        ),
        (LANGUAGE_MODULE, "EffectAttemptReconciliationConflict", None),
        (LANGUAGE_MODULE, "EffectAttemptReconciliationDenied", None),
        (LANGUAGE_MODULE, "EffectAttemptReconciliationNotFound", None),
        (LANGUAGE_MODULE, "ReconcileEffectAttempt", None),
        (LANGUAGE_MODULE, "RuntimeEffectObserver", None),
        (LANGUAGE_MODULE, "_valid_reconcile_command", None),
        (
            "control_plane_kit_operations.effect_attempts",
            "EffectAttemptRecord",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "EffectAttemptOutcomeRecord",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "ObservedEffectOutcome",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "effect_outcome_failure",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "effect_outcome_transition",
            None,
        ),
        (
            "control_plane_kit_operations.records",
            "ActivityRunRecord",
            None,
        ),
        (
            "control_plane_kit_operations.records",
            "ExecutionRequestRecord",
            None,
        ),
        (
            "control_plane_kit_operations.records",
            "OperationsRecordError",
            None,
        ),
        (
            "control_plane_kit_operations.runtime_authorities",
            "RegisteredRuntimeAuthority",
            None,
        ),
        (
            "control_plane_kit_operations.runtime_authorities",
            "RuntimeAuthorityNotFound",
            None,
        ),
        (
            "control_plane_kit_operations.runtime_authorities",
            "RuntimeAuthorityRegistrationError",
            None,
        ),
        (
            "control_plane_kit_operations.runtime_effects",
            "required_secret_uses_for_runtime_effect",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "AuthorizeSecretUse",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "SecretProviderAuthorizationDenied",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "SecretProviderRegistrationError",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "SecretUseAuthorizationConflict",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "SecretUseAuthorizationService",
            None,
        ),
        (
            "control_plane_kit_operations.secret_providers",
            "secret_use_correlation_for",
            None,
        ),
        (
            "control_plane_kit_operations.workflows",
            "InvalidOperationCommand",
            None,
        ),
        ("typing", "Any", None),
        ("typing", "Callable", None),
    )
)
EXACT_INTERPRETER_CALLS = tuple(
    architecture_testing.ResolvedCallTarget(value)
    for value in (
        "_attempt_for_update",
        "_authorize_required_secrets",
        "_existing_fold",
        "_fresh_observed_fold",
        "_request_for_update",
        "_require_current_claim",
        "_require_historical_lineage",
        "_run_for_request_for_update",
        "control_plane_kit_core.runtime_effect_observation.RuntimeEffectObservationRequest",
        "control_plane_kit_core.runtime_effect_observation.runtime_effect_request_for_intent",
        "control_plane_kit_operations.effect_attempt_fold.ExistingFold",
        "control_plane_kit_operations.effect_attempt_fold.FoldEffectAttempt",
        "control_plane_kit_operations.effect_attempt_fold.GuardedObservedEffectFold",
        *((
            "control_plane_kit_operations.effect_attempt_reconciliation."
            "EffectAttemptReconciliationConflict",
        ) * 9),
        *((
            "control_plane_kit_operations.effect_attempt_reconciliation."
            "EffectAttemptReconciliationDenied",
        ) * 6),
        *((
            "control_plane_kit_operations.effect_attempt_reconciliation."
            "EffectAttemptReconciliationNotFound",
        ) * 3),
        "control_plane_kit_operations.effect_attempt_reconciliation._valid_reconcile_command",
        "control_plane_kit_operations.effect_outcome_evidence.ObservedEffectOutcome",
        "control_plane_kit_operations.effect_outcome_evidence.effect_outcome_failure",
        "control_plane_kit_operations.effect_outcome_evidence.effect_outcome_transition",
        "control_plane_kit_operations.runtime_effects.required_secret_uses_for_runtime_effect",
        "control_plane_kit_operations.secret_providers.AuthorizeSecretUse",
        "control_plane_kit_operations.secret_providers.SecretUseAuthorizationService",
        "control_plane_kit_operations.secret_providers.secret_use_correlation_for",
        "control_plane_kit_operations.workflows.InvalidOperationCommand",
        "self._fold_service.execute_observed",
        "self._observer.observe",
        "self._secret_use_authorizer.authorize_resolution",
        "self._unit_of_work_factory",
        "stores.effect_attempt_intents.get",
        "stores.effect_attempts.get_for_update",
        "stores.effect_outcomes.get",
        "stores.execution.get_request_for_update",
        "stores.execution.get_run_for_request_for_update",
        "stores.execution.observe_request_lease_for_update",
        "stores.runtime_authorities.get_active_for_update",
        *("type",) * 14,
    )
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

    def test_shared_policy_values_accept_the_exact_reconciliation_surfaces(self) -> None:
        policies = self._architecture_policies()
        self.assertEqual(len(policies), 4)
        self.assertEqual(
            {type(policy) for policy in policies},
            {
                architecture_testing.ExactImportSurfacePolicy,
                architecture_testing.ExactCallSurfacePolicy,
            },
        )

    def test_closed_import_and_lexical_call_surface_is_effect_free(self) -> None:
        policies = self._architecture_policies()
        facts = []
        for module_name, source_path in (
            (LANGUAGE_MODULE, LANGUAGE_SOURCE_PATH),
            (INTERPRETER_MODULE, INTERPRETER_SOURCE_PATH),
        ):
            path = PACKAGE_ROOT / "src" / source_path
            self.assertTrue(
                path.is_file(),
                f"missing reconciliation module: {module_name}",
            )
            facts.append(
                architecture_testing.analyze_source(
                    path.read_text(encoding="utf-8"),
                    path=source_path,
                    module=module_name,
                )
            )
        findings = architecture_testing.evaluate_policies(
            tuple(facts),
            policies,
        )
        self.assertEqual(findings, ())

    @staticmethod
    def _architecture_policies():
        values = []
        for slug, module_name, source_path, imports, calls in (
            (
                "effect-attempt-reconciliation",
                LANGUAGE_MODULE,
                LANGUAGE_SOURCE_PATH,
                EXACT_LANGUAGE_IMPORTS,
                EXACT_LANGUAGE_CALLS,
            ),
            (
                "effect-attempt-reconciliation-interpreter",
                INTERPRETER_MODULE,
                INTERPRETER_SOURCE_PATH,
                EXACT_INTERPRETER_IMPORTS,
                EXACT_INTERPRETER_CALLS,
            ),
        ):
            values.extend(
                (
                    architecture_testing.ExactImportSurfacePolicy(
                        architecture_testing.PolicyId(
                            f"cpk.operations.{slug}.imports"
                        ),
                        architecture_testing.RuleId("exact"),
                        source_path,
                        module_name,
                        imports,
                        "reconciliation import surface differs",
                    ),
                    architecture_testing.ExactCallSurfacePolicy(
                        architecture_testing.PolicyId(
                            f"cpk.operations.{slug}.calls"
                        ),
                        architecture_testing.RuleId("exact"),
                        source_path,
                        module_name,
                        calls,
                        "reconciliation lexical call surface differs",
                    ),
                )
            )
        return tuple(values)


if __name__ == "__main__":
    unittest.main()
