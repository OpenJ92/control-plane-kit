"""#1846 strengthened closed-command, current authority and effect boundary laws."""
from dataclasses import FrozenInstanceError, fields, replace
import ast
import inspect
from pathlib import Path
import unittest

import control_plane_kit_operations as operations
from tests.health_effect_preparation_fixture import forged_copy
from tests.health_effect_start_fixture import HEALTH_SCOPES, HealthEffectStartValues, trusted_health_context
from tests.health_signing_authority_fixture import health_signing_api


class HealthSigningAuthorityContractTests(HealthEffectStartValues, unittest.TestCase):
    def command(self, api, **changes):
        first = self.health_start_value()
        values = dict(request_id=first.request_id, identity=first.transition.identity,
            context=trusted_health_context(), authority=first.authority, fence=first.fence)
        return api.ReloadHealthSigningAuthority(**(values | changes))

    def no_uow(self):
        self.fail("invalid health reload opened a transaction")

    def test_closed_exports_and_reference_only_surface(self):
        first = self.health_start_value()  # existing values before absent-module guard
        self.assertEqual(first.transition.identity.attempt, 1)
        api = health_signing_api(self)
        expected = {
            "ReloadHealthSigningAuthority": ("request_id", "identity", "context", "authority", "fence"),
            "HealthSigningAuthorityPair": ("preparation", "transit", "workload"),
            "GatewayNodeHealthReadTransitSigningAuthority": ("public_key", "resolution_grant"),
            "WorkloadNodeHealthReadSigningAuthority": ("public_key", "resolution_grant"),
        }
        for name, names in expected.items():
            value = getattr(api, name)
            self.assertIs(getattr(operations, name), value)
            self.assertEqual(tuple(field.name for field in fields(value)), names)
        for name in ("HealthSigningAuthorityError", "HealthSigningAuthorityUnavailable", "HealthSigningAuthorityReloadService"):
            self.assertIs(getattr(operations, name), getattr(api, name))
        self.assertEqual(tuple(inspect.signature(api.HealthSigningAuthorityReloadService).parameters), ("unit_of_work_factory",))
        command = self.command(api)
        self.assertFalse(hasattr(command, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            command.request_id = "changed"
        for value in (command.request_id, command.context.actor_id, command.authority.worker_id):
            self.assertNotIn(value, repr(command))

    def test_each_actor_scope_and_worker_scope_is_independent_before_uow(self):
        self.health_start_value()
        api = health_signing_api(self)
        service = api.HealthSigningAuthorityReloadService(self.no_uow)
        for scope in HEALTH_SCOPES:
            command = self.command(api, context=trusted_health_context(scopes=tuple(x for x in HEALTH_SCOPES if x is not scope)))
            with self.subTest(scope=scope), self.assertRaises(api.HealthSigningAuthorityUnavailable) as caught:
                service.execute(command)
            self.assert_safe(caught.exception)
        command = self.command(api)
        with self.assertRaises(api.HealthSigningAuthorityUnavailable):
            service.execute(replace(command, authority=replace(command.authority, scopes=())))

    def test_forged_nominal_context_worker_fence_and_identity_fail_before_uow(self):
        self.health_start_value()
        api = health_signing_api(self)
        valid = self.command(api)
        cases = (
            dict(request_id="credential-canary\n"),
            dict(context=forged_copy(valid.context, subclass=True)),
            dict(context=forged_copy(trusted_health_context(scopes=()), granted_scopes=HEALTH_SCOPES)),
            dict(context=trusted_health_context(actor="Invalid-Secret-Actor/Canary")),
            dict(authority=forged_copy(valid.authority, scopes=("execution:operate",))),
            dict(fence=forged_copy(valid.fence, generation=True)),
            dict(identity=forged_copy(valid.identity, attempt=True)),
            dict(identity=replace(valid.identity, attempt=2)),
            dict(authority=replace(valid.authority, worker_id="other-worker")),
        )
        service = api.HealthSigningAuthorityReloadService(self.no_uow)
        for changes in cases:
            with self.subTest(changes=tuple(changes)), self.assertRaises(api.HealthSigningAuthorityError) as caught:
                service.execute(forged_copy(valid, **changes))
            self.assert_safe(caught.exception, "credential-canary", "Invalid-Secret-Actor/Canary")

    def test_new_module_has_no_external_effect_imports(self):
        self.health_start_value()
        api = health_signing_api(self)
        source = Path(api.__file__).read_text()
        tree = ast.parse(source)
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        for forbidden in ("requests", "httpx", "docker", "subprocess", "socket", "control_plane_kit_interpreters"):
            self.assertFalse(any(name == forbidden or name.startswith(forbidden + ".") for name in modules))
        self.assertNotIn("authorize_secret_use_in_unit_of_work", source)
        self.assertNotIn("epoch_clock", inspect.signature(api.HealthSigningAuthorityReloadService).parameters)
