"""#1852 new-law public entrance, trusted mapping and closed result checks."""
from dataclasses import FrozenInstanceError, fields, replace
import ast
from pathlib import Path
import inspect
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied, ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationRecord
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_record_fixture import EffectAttemptRecordFixture
from tests.health_effect_preparation_fixture import forged_copy
from tests.health_effect_start_fixture import HEALTH_SCOPES, HealthEffectStartValues, trusted_health_context


class HealthEffectAttemptStartContractTests(HealthEffectStartValues, EffectAttemptRecordFixture, unittest.TestCase):
    def fail_uow(self):
        self.fail("invalid health command opened a unit of work")

    def service(self):
        return EffectAttemptStartService(self.fail_uow,
            id_factory=lambda: self.fail("invalid health command allocated an ID"))

    def test_closed_command_exports_and_additive_service_surface(self):
        start = self.health_start_value()
        context = trusted_health_context()
        api = self.health_start_api()
        command = api.StartHealthEffectAttempt(start, context)
        self.assertIs(operations.StartHealthEffectAttempt, api.StartHealthEffectAttempt)
        self.assertIs(operations.HealthEffectAttemptStartResult, api.HealthEffectAttemptStartResult)
        self.assertEqual(tuple(item.name for item in fields(command)), ("start", "context"))
        self.assertTrue(all(not item.repr for item in fields(command)))
        self.assertEqual(command.start, start)
        self.assertEqual(command.context, context)
        with self.assertRaises(FrozenInstanceError):
            command.context = context
        self.assertEqual(tuple(inspect.signature(EffectAttemptStartService).parameters),
            ("unit_of_work_factory", "id_factory", "health_receiver_decoders"))
        self.assertEqual(tuple(inspect.signature(EffectAttemptStartService.execute_health).parameters), ("self", "command"))
        self.assertEqual(tuple(inspect.signature(EffectAttemptStartService.execute).parameters), ("self", "command"))

    def test_each_context_scope_and_execution_scope_are_independent(self):
        start = self.health_start_value()
        api = self.health_start_api()
        for missing in HEALTH_SCOPES:
            with self.subTest(missing=missing.value):
                context = trusted_health_context(scopes=tuple(scope for scope in HEALTH_SCOPES if scope is not missing))
                command = api.StartHealthEffectAttempt(start, context)
                with self.assertRaises(EffectAttemptStartDenied) as caught:
                    self.service().execute_health(command)
                self.assert_safe_error(caught.exception)
        command = api.StartHealthEffectAttempt(replace(start, authority=replace(start.authority, scopes=())), trusted_health_context())
        with self.assertRaises(EffectAttemptStartDenied) as caught:
            self.service().execute_health(command)
        self.assert_safe_error(caught.exception)

    def test_nonhealth_dedicated_command_is_rejected_before_uow(self):
        from control_plane_kit_core.planning import StartRuntime, RuntimeTarget
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        start = self.health_start_value()
        intent = replace(start.intent, operation=StartRuntime(RuntimeTarget("docker")))
        nonhealth = replace(start, intent=intent, transition=replace(start.transition,
            request_fingerprint=runtime_effect_intent_fingerprint(intent)))
        api = self.health_start_api()
        with self.assertRaises(InvalidOperationCommand) as caught:
            command = api.StartHealthEffectAttempt(nonhealth, trusted_health_context())
            self.service().execute_health(command)
        self.assert_safe_error(caught.exception)

    def test_valid_core_actor_outside_secret_domain_has_no_fallback(self):
        start = self.health_start_value()
        context = trusted_health_context(actor="Valid-Core-Subject/Canary")
        self.assertIs(type(context), TrustedCommandContext)
        self.assertEqual(context.actor_id, "Valid-Core-Subject/Canary")
        api = self.health_start_api()
        with self.assertRaises(InvalidOperationCommand) as caught:
            command = api.StartHealthEffectAttempt(start, context)
            self.service().execute_health(command)
        self.assert_safe_error(caught.exception, context.actor_id)

    def test_context_workspace_and_forged_grants_are_rejected_before_uow(self):
        start = self.health_start_value()
        api = self.health_start_api()
        accepted = trusted_health_context()
        principal = accepted.principal
        grant = principal.workspace_grants[0]
        contexts = (
            trusted_health_context(workspace="foreign-workspace-canary"),
            forged_copy(trusted_health_context(scopes=()), granted_scopes=HEALTH_SCOPES),
            forged_copy(trusted_health_context(), subclass=True),
            forged_copy(trusted_health_context(), principal=forged_copy(trusted_health_context().principal, subclass=True)),
            forged_copy(accepted, principal=forged_copy(principal, identity=forged_copy(principal.identity, kind="operator"))),
            forged_copy(accepted, principal=forged_copy(principal, identity=forged_copy(principal.identity, subject_id=7))),
            forged_copy(accepted, principal=forged_copy(principal, workspace_grants=(forged_copy(grant, workspace_id="foreign-workspace-canary"),))),
            forged_copy(accepted, principal=forged_copy(principal, workspace_grants=(forged_copy(grant, scopes=("secret-provider:use",)),))),
        )
        for context in contexts:
            with self.subTest(context_type=type(context).__name__):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    command = api.StartHealthEffectAttempt(start, context)
                    self.service().execute_health(command)
                self.assert_safe_error(caught.exception, "foreign-workspace-canary")

    def test_hostile_outer_command_is_refused_without_virtual_access(self):
        api = self.health_start_api()
        dispatches = []
        class Hostile:
            def __getattribute__(self, name):
                dispatches.append(name)
                raise AssertionError("hostile health command accessed")
        with self.assertRaises(InvalidOperationCommand) as caught:
            self.service().execute_health(Hostile())
        self.assert_safe_error(caught.exception)
        self.assertEqual(dispatches, [])
        command = api.StartHealthEffectAttempt(self.health_start_value(), trusted_health_context())
        with self.assertRaises(InvalidOperationCommand):
            self.service().execute_health(forged_copy(command, subclass=True))

    def test_result_is_frozen_and_joins_exact_original_attempt_evidence(self):
        material = self.material()
        attempt = self.record(activity_id=material["identity"].activity_id)
        material.update(identity=attempt.state.identity, request_fingerprint=attempt.state.request_fingerprint,
            original_event_id=attempt.original_start_event.event_id)
        preparation = HealthEffectPreparationRecord(**material)
        api = self.health_start_api()
        result = api.HealthEffectAttemptStartResult(NewlyStarted(attempt), preparation)
        self.assertTrue(all(not item.repr for item in fields(result)))
        with self.assertRaises(FrozenInstanceError):
            result.preparation = preparation
        for changed in (
            replace(preparation, request_fingerprint="f" * 64),
            replace(preparation, original_event_id="foreign-original-event-canary"),
            forged_copy(preparation, subclass=True),
        ):
            with self.subTest(changed=type(changed).__name__):
                with self.assertRaises(OperationsRecordError) as caught:
                    api.HealthEffectAttemptStartResult(NewlyStarted(attempt), changed)
                self.assert_safe_error(caught.exception, "foreign-original-event-canary")

    def test_existing_result_can_retain_evolved_attempt_without_relabeling(self):
        material = self.material()
        attempt = self.record("succeeded", activity_id=material["identity"].activity_id,
            original_time="2030-01-01T00:00:00Z", latest_time="2030-01-01T00:00:01Z")
        material.update(identity=attempt.state.identity, request_fingerprint=attempt.state.request_fingerprint,
            original_event_id=attempt.original_start_event.event_id)
        preparation = HealthEffectPreparationRecord(**material)
        api = self.health_start_api()
        result = api.HealthEffectAttemptStartResult(ExistingAttempt(attempt), preparation)
        self.assertIs(type(result.start), ExistingAttempt)
        self.assertEqual(result.start.attempt, attempt)
        self.assertNotEqual(attempt.latest_transition_event, attempt.original_start_event)
        self.assertEqual(result.preparation.original_event_id, attempt.original_start_event.event_id)

    def test_new_health_modules_have_no_provider_signing_dispatch_or_independent_clock_boundary(self):
        self.health_start_api()
        source = Path(__file__).resolve().parents[1] / "src" / "control_plane_kit_operations"
        allowed_standard = {"__future__", "dataclasses", "datetime", "re", "typing", "collections.abc"}
        allowed_operations = {
            "control_plane_kit_operations.effect_attempt_start",
            "control_plane_kit_operations.effect_attempts",
            "control_plane_kit_operations.health_effect_preparations",
            "control_plane_kit_operations.health_effect_attempt_start",
            "control_plane_kit_operations._health_receiver_trust",
            "control_plane_kit_operations.execution_leases",
            "control_plane_kit_operations.lifecycle",
            "control_plane_kit_operations.records",
            "control_plane_kit_operations.workflows",
            "control_plane_kit_operations.runtime_management_targets",
            "control_plane_kit_operations.delegation_signing_keys",
            # This is reference authorization, not a provider client.
            "control_plane_kit_operations.secret_providers",
        }
        forbidden_calls = {"sign", "sign_bytes", "sign_grant", "resolve_secret", "dispatch",
            "provider_request", "provider_result", "now", "utcnow", "time", "monotonic",
            "commit", "rollback", "connect", "urlopen", "request"}
        for filename in ("health_effect_attempt_start.py", "_health_effect_attempt_start.py"):
            with self.subTest(module=filename):
                self.assertTrue((source / filename).is_file(), "health-owned module is missing")
                tree = ast.parse((source / filename).read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        imports = (node.module or "",)
                        self.assertEqual(node.level, 0, "health boundary uses explicit owner imports")
                    elif isinstance(node, ast.Import):
                        imports = tuple(alias.name for alias in node.names)
                    else:
                        imports = ()
                    for imported in imports:
                        self.assertTrue(imported in allowed_standard or imported in allowed_operations
                            or imported.startswith("control_plane_kit_core."), imported)
                    if isinstance(node, ast.Call):
                        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                            node.func.id if isinstance(node.func, ast.Name) else "")
                        self.assertNotIn(name, forbidden_calls)
