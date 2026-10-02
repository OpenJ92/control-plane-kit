"""#1918 representation and translation refusal; no allocation state machine."""
from dataclasses import replace
import unittest

import rfc8785

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context, _runtime_effect_intent_for_context
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.configuration_instance_fixture import configuration_language, configuration_ref, configuration_cleanup_activity
from tests.effect_attempt_intent_fixture import (
    EffectAttemptIntentFixture, _encode_runtime_effect_intent, _decode_runtime_effect_intent, product_material,
)
from tests.test_runtime_effect_translation import _context


class ConfigurationInstanceIntentTests(EffectAttemptIntentFixture, unittest.TestCase):
    def configuration_intent(self):
        m = configuration_language()
        self.assertTrue(hasattr(RuntimeEffectKind, "CONFIGURATION_ACTIVITY_V1"),
                        "missing #1918 configuration-activity.v1 capability")
        artifact = ConfigurationArtifact("service-config", "/etc/service/config.json",
            ConfigurationMediaType.JSON, '{"workers":2}')
        material = product_material()
        material = replace(material, product=replace(material.product,
            runtime_contract=replace(material.product.runtime_contract, configuration_artifacts=(artifact,))))
        return replace(self.intent(products=(material,), process_delivery=False),
            kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            configuration_instances=m.ConfigurationInstanceSelection((configuration_ref(digest=artifact.content_digest),)))

    def test_immutable_intent_preimage_preserves_exact_profile_selection_and_fingerprint(self):
        intent = self.configuration_intent()
        canonical = _encode_runtime_effect_intent(intent)
        reconstructed = _decode_runtime_effect_intent(canonical)
        self.assertEqual(canonical, rfc8785.dumps(intent.descriptor()))
        self.assertEqual(reconstructed, intent)
        self.assertEqual(reconstructed.configuration_instances, intent.configuration_instances)
        self.assertEqual(runtime_effect_intent_fingerprint(reconstructed), runtime_effect_intent_fingerprint(intent))
        changed = replace(intent, configuration_instances=configuration_language().ConfigurationInstanceSelection((
            replace(intent.configuration_instances.instances[0], allocation_id="other-incarnation"),)))
        self.assertNotEqual(_encode_runtime_effect_intent(changed), canonical)

    def test_persisted_intent_refuses_null_unknown_or_dropped_new_semantics(self):
        intent = self.configuration_intent()
        descriptor = intent.descriptor()
        absent = {k: v for k, v in descriptor.items() if k != "configuration_instances"}
        variants = [absent, {**descriptor, "configuration_instances": None},
            {**descriptor, "kind": "realize-activity"},
            {**descriptor, "configuration_instances": {**descriptor["configuration_instances"], "profile": "unknown"}},
            {**descriptor, "configuration_instances": {**descriptor["configuration_instances"], "extra": "canary-extra"}},
            {**self.intent().descriptor(), "configuration_instances": None}]
        for candidate in variants:
            with self.subTest(candidate_keys=tuple(candidate)):
                self.assert_intent_error(lambda: _decode_runtime_effect_intent(rfc8785.dumps(candidate)), "canary-extra")
        document = _encode_runtime_effect_intent(intent)
        self.assert_intent_error(lambda: _decode_runtime_effect_intent(b" " + document))
        duplicate = document[:-1] + b',"configuration_instances":null}'
        self.assert_intent_error(lambda: _decode_runtime_effect_intent(duplicate))

    def test_cleanup_preimage_preserves_original_candidates_without_current_products(self):
        activity = configuration_cleanup_activity()
        self.assertTrue(hasattr(RuntimeEffectKind, "CONFIGURATION_ACTIVITY_V1"))
        intent = replace(self.intent(products=(), process_delivery=False),
            kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, operation=activity.operation)
        self.assertEqual(_decode_runtime_effect_intent(_encode_runtime_effect_intent(intent)), intent)
        self.assertNotIn("configuration_instances", intent.descriptor())
        self.assertEqual(intent.products, ())


class ConfigurationCleanupTranslationTests(unittest.TestCase):
    def test_valid_cleanup_refuses_translation_explicitly_before_generic_target_access(self):
        activity = configuration_cleanup_activity()
        context = _context(activity=activity)
        for translate in (lambda: runtime_effect_request_for_context(context),
                          lambda: _runtime_effect_intent_for_context(context, activity)):
            with self.assertRaisesRegex(InvalidOperationCommand, "configuration cleanup.*unsupported"):
                translate()
