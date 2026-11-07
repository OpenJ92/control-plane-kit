"""Supplied-fact congruence only: no committed issuance, admission or acquisition."""
from dataclasses import replace
import importlib
import importlib.util
import unittest

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import WaitForHealthy
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference, SecretProviderId, SecretReference,
    http_verification_sample_signing_intent_for,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisterDelegationSigningKeyCommand,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    SecretProviderKind, authorized_secret_use_for, secret_resolution_grant_for,
)
from tests import test_http_sample_compatibility as compatible
from tests.health_receiver_trust_fixture import public_key

MODULE = "control_plane_kit_operations.http_sample_sender_input"
NOW = "2026-11-07T12:00:00Z"
PURPOSES = dict(transit=DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT,
    workload=DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE)


def api(test):
    test.assertIsNotNone(importlib.util.find_spec(MODULE), "one-check supplied sender-input join is missing")
    return importlib.import_module(MODULE)


def supplied_case(test, *, provider_overrides=None, key_overrides=None, **changes):
    """Pure record constructors do not establish that a check was reached/committed."""
    values, reference, slot = compatible.case(test)
    binding = compatible.HttpSampleCompatibilityTests.bind(test, values, reference, slot)
    activity = next(item for item in values["plan"].plan.activities
        if type(item.operation) is WaitForHealthy and item.operation.target.node_id == values["node_id"])
    context = dict(compatibility=binding, plan=values["plan"], check_id="ready",
        attempt=EffectAttemptIdentity(RunId("run-a"), activity.activity_id.value, 1),
        operation_id="request-a", session_id=values["plan"].session_id, actor_subject="operator-a",
        effect_id="event-a", request_fingerprint="a" * 64)
    context.update(changes)
    roles = {}
    for role, purpose in PURPOSES.items():
        private = SecretReference("secret://workspace-secrets/workspace-a/" + role)
        key = RegisterDelegationSigningKeyCommand(**(dict(workspace_id="workspace-a", purpose=purpose,
            issuer="sample-controller", public_key=public_key(role), private_key_reference=private,
            admitted_by="operator-a", admitted_at=NOW, actor_scopes=()) | (key_overrides or {}).get(role, {}))).candidate()
        provider = RegisterSecretProviderCommand(**(dict(workspace_id="workspace-a",
            provider_id=SecretProviderId("workspace-secrets"), provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Workspace secrets", endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
            credential_reference=SecretReference("secret://workspace-secrets/workspace-a/provider-credential"),
            allowed_reference_prefixes=(SecretReference("secret://workspace-secrets/workspace-a"),),
            allowed_intents=tuple(http_verification_sample_signing_intent_for(p) for p in PURPOSES.values()),
            admitted_by="operator-a", admitted_at=NOW, actor_scopes=()) | (provider_overrides or {}).get(role, {}))).candidate()
        ref = RegisterSecretReferenceCommand(workspace_id="workspace-a", reference=private,
            provider_registration_id=provider.registration_id,
            allowed_intents=(http_verification_sample_signing_intent_for(purpose),),
            admitted_by="operator-a", admitted_at=NOW, actor_scopes=()).candidate()
        correlation, probe = api(test).http_sample_signing_binding_for(**context,
            purpose=purpose, key_registration_id=key.registration_id)
        command = AuthorizeSecretUse(workspace_id="workspace-a", reference=private,
            intent=http_verification_sample_signing_intent_for(purpose), actor_subject=context["actor_subject"],
            correlation_id=correlation, requested_at=NOW, actor_scopes=(),
            operation_id=context["operation_id"], session_id=context["session_id"],
            run_id=context["attempt"].run_id.value, activity_id=context["attempt"].activity_id,
            effect_id=context["effect_id"], probe_id=probe)
        use = authorized_secret_use_for(command, reference=ref, provider=provider)
        roles.update({role + "_key": key, role + "_use": use, role + "_provider": provider})
    return context, roles


class HttpSampleSenderInputTests(unittest.TestCase):
    def test_internally_consistent_provider_policy_still_must_cover_its_use(self):
        for role, purpose in PURPOSES.items():
            other = next(value for value in PURPOSES.values() if value is not purpose)
            for policy in (dict(allowed_intents=(http_verification_sample_signing_intent_for(other),)),
                    dict(allowed_reference_prefixes=(SecretReference("secret://workspace-secrets/foreign"),))):
                with self.subTest(role=role, policy=tuple(policy)):
                    context, roles = supplied_case(self, provider_overrides={role: policy})
                    # Candidate provider/reference/use identities are all rebuilt;
                    # the pure constructors do not check this policy consistency.
                    self.refuses(context, roles)

    def test_sender_wire_context_refuses_operations_valid_unaddressable_ids(self):
        context, _ = supplied_case(self)
        cases = [dict(attempt=replace(context["attempt"], run_id=RunId("localhost"))),
            dict(effect_id="localhost")]
        for field, plan_field in (("desired_graph_id", "desired_graph_id"),
                ("projection_id", "desired_realized_projection_id")):
            cases.append(dict(compatibility=replace(context["compatibility"], **{field: "localhost"}),
                plan=replace(context["plan"], **{plan_field: "localhost"})))
        for changes in cases:
            with self.subTest(changes=tuple(changes)), self.assertRaises(api(self).HttpSampleSenderInputUnavailable):
                supplied, roles = supplied_case(self, **changes)
                api(self).bind_http_sample_sender_input(**supplied, **roles)

    def test_operations_only_actor_identity_need_not_fit_sample_wire(self):
        try:
            context, roles = supplied_case(self, actor_subject="localhost")
            result = api(self).bind_http_sample_sender_input(**context, **roles)
        except api(self).HttpSampleSenderInputUnavailable:
            self.fail("Operations-owned actor identity must retain its existing domain")
        self.assertEqual(result.actor_subject, "localhost")
        self.assertEqual(result.workload_grant.actor_subject, "localhost")

    def test_supplied_matching_roles_project_existing_grants_for_original_check(self):
        context, roles = supplied_case(self)
        result = api(self).bind_http_sample_sender_input(**context, **roles)
        self.assertIs(result.compatibility, context["compatibility"])
        self.assertEqual(result.check, next(c.check for c in result.compatibility.receiver.checks if c.check.check_id == "ready"))
        self.assertEqual(result.attempt, context["attempt"])
        for role in PURPOSES:
            self.assertIs(getattr(result, role + "_key"), roles[role + "_key"])
            self.assertEqual(getattr(result, role + "_grant"), secret_resolution_grant_for(
                roles[role + "_use"], provider=roles[role + "_provider"]))
        # VERIFY_ONLY keys deliberately demonstrate this is not current authority.
        self.assertEqual(result.transit_key.status.value, "verify-only")
        self.assertNotEqual(result.transit_grant.correlation_id, result.workload_grant.correlation_id)

    def test_missing_or_swapped_roles_refuse_without_partial_output(self):
        context, roles = supplied_case(self)
        candidates = [roles | {name: None} for name in ("transit_key", "workload_use", "transit_provider")]
        candidates.append({name: roles[("workload" if name.startswith("transit") else "transit") + name[name.index("_"):]]
            for name in roles})
        for candidate in candidates:
            with self.subTest(candidate=tuple(name for name, value in candidate.items() if value is None)):
                self.refuses(context, candidate)

    def test_cross_check_effect_attempt_and_actor_context_refuse(self):
        context, roles = supplied_case(self)
        for changes in (dict(check_id="live"), dict(effect_id="event-b"), dict(actor_subject="operator-b"),
                dict(operation_id="request-b"), dict(request_fingerprint="b" * 64),
                dict(attempt=replace(context["attempt"], attempt=2))):
            other, other_roles = supplied_case(self, **changes)
            with self.subTest(changes=tuple(changes)):
                # Each role is independently well-formed for its own context.
                self.refuses(context, roles | {name: value for name, value in other_roles.items() if name.startswith("workload")})
                self.refuses(other, roles)
        self.refuses(context | dict(session_id="session-b"), roles)
        other_activity = next(item for item in context["plan"].plan.activities
            if item.activity_id.value != context["attempt"].activity_id)
        self.refuses(context | dict(attempt=replace(context["attempt"], activity_id=other_activity.activity_id.value)), roles)

    def test_original_registration_and_same_key_id_different_fingerprint_refuse(self):
        context, roles = supplied_case(self)
        key = roles["workload_key"]
        for changes in (dict(registration_id=roles["transit_key"].registration_id),
                dict(public_key=public_key("other", key_id=key.public_key.key_id)),
                dict(issuer="other-issuer"),
                dict(private_key_reference=SecretReference("secret://workspace-secrets/workspace-a/replacement"))):
            with self.subTest(changes=tuple(changes)):
                self.refuses(context, roles | {"workload_key": replace(key, **changes)})
        # A fully valid replacement registration cannot replace the original probe binding.
        replacement = RegisterDelegationSigningKeyCommand(workspace_id=key.workspace_id, purpose=key.purpose,
            issuer=key.issuer, public_key=key.public_key,
            private_key_reference=SecretReference("secret://workspace-secrets/workspace-a/replacement"),
            admitted_by="operator-a", admitted_at=NOW, actor_scopes=()).candidate()
        self.refuses(context, roles | {"workload_key": replacement})
        original_binding = api(self).http_sample_signing_binding_for(**context,
            purpose=key.purpose, key_registration_id=key.registration_id)
        replacement_binding = api(self).http_sample_signing_binding_for(**context,
            purpose=key.purpose, key_registration_id=replacement.registration_id)
        self.assertEqual(original_binding[0], replacement_binding[0])
        self.assertNotEqual(original_binding[1], replacement_binding[1])
        # Rebuild the key registration, probe and use consistently as well: an
        # uninstalled fingerprint must still refuse despite the same key ID.
        other_context, other_roles = supplied_case(self, key_overrides={"workload": dict(
            public_key=public_key("other", key_id=key.public_key.key_id))})
        self.refuses(other_context, other_roles)

    def test_provider_substitution_and_inconsistent_authorization_identity_refuse(self):
        context, roles = supplied_case(self)
        use = roles["transit_use"]
        for changes in (dict(provider_registration_id="other-provider"),
                dict(reference_registration_id="other-reference"), dict(probe_id="other-check")):
            with self.subTest(changes=tuple(changes)):
                self.refuses(context, roles | {"transit_use": replace(use, **changes)})
        self.refuses(context, roles | {"transit_provider": replace(roles["transit_provider"],
            endpoint_reference=SecretProviderEndpointReference("other-endpoint"))})

    def refuses(self, context, roles):
        with self.assertRaises(api(self).HttpSampleSenderInputUnavailable) as caught:
            api(self).bind_http_sample_sender_input(**context, **roles)
        self.assertEqual(str(caught.exception), "HTTP sample sender input is unavailable")
        self.assertIsNone(caught.exception.__context__)
