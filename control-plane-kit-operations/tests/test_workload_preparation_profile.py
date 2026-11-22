"""Actual workload roles retain exact versions without acquiring probe roles."""

from dataclasses import replace
import hashlib
import unittest

import rfc8785

from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_for_request, runtime_effect_intent_fingerprint
from control_plane_kit_operations.prepared_runtime_invocations import (
    prepared_runtime_invocation_profile, prepared_runtime_observation_supported,
    runtime_preparation_candidate, workload_preparation_candidate,
)
from control_plane_kit_operations.runtime_secret_uses import (
    required_prepared_runtime_secret_uses, required_secret_uses_for_runtime_effect,
)
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from tests.test_secret_version_dispatch import selected_context


class WorkloadPreparationProfileTests(unittest.TestCase):
    def test_local_target_commits_consumer_version_but_not_unconsumed_probe(self):
        context = selected_context(with_authority=True, shared_probe=True)
        request = runtime_effect_request_for_context(context)
        authority, = context.runtime_authorities
        intent = runtime_effect_intent_for_request(request)
        selection, = request.products[0].secret_selections
        self.assertEqual(len(required_secret_uses_for_runtime_effect(request, authority)), 2)
        self.assertEqual(required_prepared_runtime_secret_uses(request, authority), (selection,))
        profile = prepared_runtime_invocation_profile(intent,
            original_event_id=request.effect_id, runtime_authority=authority)
        expected = dict(domain="cpk.prepared-runtime-final-target.v1", kind="prepared-runtime-dispatch",
            request_fingerprint=runtime_effect_intent_fingerprint(intent), workspace_id=intent.source.workspace_id,
            runtime_id=request.products[0].runtime_id, runtime_kind="docker",
            authority_ref=intent.authority_ref.reference_id, runtime_registration_id=authority.registration_id,
            required_uses=[["consumer-version", selection.reference.reference_id, selection.intent.value, selection.version_id]])
        self.assertEqual(profile.target_fingerprint, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        self.assertFalse(prepared_runtime_observation_supported(intent))

    def test_authorityless_consumer_routes_to_explicit_refusal_and_malformed_material_cannot_fallback(self):
        request = runtime_effect_request_for_context(selected_context())
        intent = runtime_effect_intent_for_request(request)
        self.assertTrue(workload_preparation_candidate(intent))
        self.assertTrue(runtime_preparation_candidate(intent))
        with self.assertRaises(ValueError):
            workload_preparation_candidate(replace(intent, products=()))
