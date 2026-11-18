"""#1918 new-law capability matrix; inherited absent-field wire laws stay literal."""
from dataclasses import replace
import unittest

import rfc8785

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType, ConfigurationFileMode
from control_plane_kit_core.planning import NodeTarget, StartNode, ReconcileNode, StopNode
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_for_request, runtime_effect_request_for_intent, runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectContractError
from tests.test_configuration_instances import instance, language
from tests.test_configuration_cleanup_contract import cleanup, cleanup_request
from tests.test_runtime_effect_intent import _request, _product_material


def selected_request(operation=None):
    m = language()
    if not hasattr(RuntimeEffectKind, "CONFIGURATION_ACTIVITY_V1"):
        raise AssertionError("missing #1918 configuration-activity.v1 kind")
    artifacts = tuple(ConfigurationArtifact(f"config-{name}", f"/etc/{name}.json",
        ConfigurationMediaType.JSON, '{"workers":2}') for name in ("a", "b"))
    material = _product_material()
    material = replace(material, product=replace(material.product,
        runtime_contract=replace(material.product.runtime_contract, configuration_artifacts=artifacts)))
    selection = m.ConfigurationInstanceSelection(tuple(instance(
        allocation_id=f"allocation-{i}", artifact_id=artifact.artifact_id, target_path=artifact.target_path,
        content_digest=artifact.content_digest) for i, artifact in enumerate(artifacts)))
    return replace(_request(complete=False), kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
        operation=operation or StartNode(NodeTarget("api")), products=(material,), configuration_instances=selection)


class ConfigurationEffectContractTests(unittest.TestCase):
    def test_start_and_reconcile_bind_selection_once_and_roundtrip_without_downgrade(self):
        for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
            request = selected_request(operation)
            intent = runtime_effect_intent_for_request(request)
            self.assertEqual(intent.configuration_instances, request.configuration_instances)
            self.assertEqual(intent.kind.value, "configuration-activity.v1")
            self.assertEqual(intent.descriptor()["configuration_instances"],
                language().ConfigurationInstanceSelectionCodec().encode(request.configuration_instances))
            self.assertEqual(request.descriptor()["configuration_instances"],
                language().ConfigurationInstanceSelectionCodec().encode(request.configuration_instances))
            self.assertNotIn("allocation_id", repr(request.products[0].descriptor()))
            reconstructed = runtime_effect_request_for_intent(intent, effect_id="event-new")
            self.assertEqual(reconstructed.configuration_instances, request.configuration_instances)
            self.assertEqual(reconstructed.effect_id, reconstructed.source.intent_event_id)
            self.assertEqual(runtime_effect_intent_for_request(reconstructed), intent)
            self.assertNotIn("event-new", repr(intent.descriptor()))
            self.assertNotIn("configuration_instances", _request(complete=False).descriptor())

    def test_every_kind_operation_selection_cell_is_closed(self):
        request = selected_request()
        intent = runtime_effect_intent_for_request(request)
        for value in (request, intent):
            for changes in (
                {"kind": RuntimeEffectKind.REALIZE_ACTIVITY},
                {"configuration_instances": None},
                {"operation": StopNode(NodeTarget("api"))},
                {"operation": StartNode(NodeTarget("foreign"))},
                {"operation": ReconcileNode(NodeTarget("foreign"))},
                {"operation": cleanup()},
                {"products": ()},
                {"products": (replace(value.products[0], node_id="other"),)},
                {"products": (value.products[0], replace(value.products[0], node_id="other"))},
            ):
                with self.subTest(value=type(value).__name__, changes=tuple(changes)), self.assertRaises(RuntimeEffectContractError):
                    replace(value, **changes)
        for value in (cleanup_request(), runtime_effect_intent_for_request(cleanup_request())):
            self.assertNotIn("configuration_instances", value.descriptor())
            for changes in ({"kind": RuntimeEffectKind.REALIZE_ACTIVITY},
                            {"configuration_instances": request.configuration_instances},
                            {"products": request.products},
                            {"source": replace(value.source, workspace_id="foreign")},
                            {"authority_ref": _request().authority_ref,
                             "authority_deliveries": _request().authority_deliveries}):
                with self.subTest(value=type(value).__name__, changes=tuple(changes)), self.assertRaises(RuntimeEffectContractError):
                    replace(value, **changes)
        self.assertEqual(_request(complete=False).kind, RuntimeEffectKind.REALIZE_ACTIVITY)

    def test_assignment_requires_all_and_only_exact_selected_material_coordinates(self):
        m = language()
        request = selected_request()
        refs = request.configuration_instances.instances
        for changes in ({"workspace_id": "foreign"}, {"runtime_id": "foreign"}, {"node_id": "foreign"},
                        {"artifact_id": "foreign"}, {"target_path": "/etc/foreign"},
                        {"content_digest": "b" * 64}, {"media_type": ConfigurationMediaType.TEXT},
                        {"file_mode": ConfigurationFileMode.OWNER_READ_ONLY}):
            candidate_refs = tuple(replace(ref, **changes) for ref in refs) if next(iter(changes)) in (
                "workspace_id", "runtime_id", "node_id") else (replace(refs[0], **changes), refs[1])
            candidate = m.ConfigurationInstanceSelection(candidate_refs)
            for value in (request, runtime_effect_intent_for_request(request)):
                with self.subTest(changes=changes, value=type(value).__name__), self.assertRaises(RuntimeEffectContractError):
                    replace(value, configuration_instances=candidate)
        for candidate in (m.ConfigurationInstanceSelection((refs[0],)),
                          m.ConfigurationInstanceSelection((*refs, instance(allocation_id="extra")))):
            with self.assertRaises(RuntimeEffectContractError):
                replace(request, configuration_instances=candidate)
        material = request.products[0]
        # Registered defaults cannot stand in for the selected product contract.
        with self.assertRaises(RuntimeEffectContractError):
            replace(request, products=(replace(material, product=replace(material.product,
                runtime_contract=replace(material.product.runtime_contract, configuration_artifacts=()))),))

    def test_allocation_identity_affects_complete_fingerprint_but_permutation_does_not(self):
        m = language()
        request = selected_request()
        intent = runtime_effect_intent_for_request(request)
        refs = request.configuration_instances.instances
        permuted = replace(intent, configuration_instances=m.ConfigurationInstanceSelection(tuple(reversed(refs))))
        self.assertEqual(runtime_effect_intent_fingerprint(permuted), runtime_effect_intent_fingerprint(intent))
        changed = replace(intent, configuration_instances=m.ConfigurationInstanceSelection((
            replace(refs[0], allocation_id="other-incarnation"), refs[1])))
        self.assertNotEqual(runtime_effect_intent_fingerprint(changed), runtime_effect_intent_fingerprint(intent))
        self.assertNotEqual(rfc8785.dumps(changed.descriptor()), rfc8785.dumps(intent.descriptor()))
