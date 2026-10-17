"""#1975 Gate 3A: actual evidence envelopes, never synthetic provider truth."""
from copy import deepcopy
from dataclasses import fields, replace
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartNode, StartRuntime, RuntimeTarget
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence, RuntimeEffectObservationFailure,
    RuntimeEffectObservedSucceeded, RuntimeEffectObservedFailed,
    RuntimeEffectObservedAbsent, RuntimeEffectObservedConflict,
    RuntimeEffectObservedIndeterminate, RuntimeEffectObserverUnsupported,
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint,
    runtime_effect_observation_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectFailure,
    RuntimeEffectKind, RuntimeEffectRequest, RuntimeEffectResult,
)
from control_plane_kit_core.secret_installation import (
    SecretInstallationReceipt, SecretInstallationDisposition,
    secret_installation_receipt_for_result, secret_material_selection_fingerprint,
)
from tests.test_configuration_installation import payload as configuration_payload
from tests.test_secret_selections import language, request_with_version


RESOLUTION = "secret_resolution_receipt"
INSTALLATION = "secret_installation_receipt"
CONFIGURATION = "configuration_installation_receipt"


def request_value(*, configuration=True, secret=True, operation=None):
    request = request_with_version()
    material = request.products[0]
    contract = material.product.runtime_contract
    if not configuration:
        contract = replace(contract, configuration_artifacts=())
    if not secret:
        contract = replace(contract, secret_deliveries=())
    material = replace(material, product=replace(material.product, runtime_contract=contract),
        secret_selections=material.secret_selections if secret else ())
    return replace(request, products=(material,),
        operation=operation or request.operation,
        kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1 if configuration else RuntimeEffectKind.REALIZE_ACTIVITY,
        configuration_instances=request.configuration_instances if configuration else None)


def receipt_evidence(request, disposition="replaced"):
    commitment = dict(
        request_fingerprint=runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)),
        selection_fingerprint=secret_material_selection_fingerprint(request),
    )
    evidence = {
        RESOLUTION: dict(profile="secret-resolution-receipt.v1", **commitment),
        INSTALLATION: dict(profile="secret-installation-receipt.v1", **commitment,
            disposition=disposition,
            prior_realization_fingerprint=None if disposition == "created" else "a" * 64,
            current_realization_fingerprint=("a" if disposition == "reused" else "b") * 64),
    }
    if request.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
        evidence[CONFIGURATION] = configuration_payload(request, disposition)
    return evidence


def observed(request, evidence, variant=RuntimeEffectObservedSucceeded):
    failure = None if variant in (RuntimeEffectObservedSucceeded, RuntimeEffectObservedAbsent) else (
        RuntimeEffectObservationFailure("fixture.unverified", "Installation was not verified."))
    return variant(request.effect_id,
        runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)),
        RuntimeEffectObservationEvidence(evidence), failure=failure)


def forged(value, *, subclass=False, **changes):
    value_type = type("Derived" + type(value).__name__, (type(value),), {}) if subclass else type(value)
    result = object.__new__(value_type)
    for item in fields(value):
        object.__setattr__(result, item.name, changes.get(item.name, getattr(value, item.name)))
    return result


class SecretInstallationObservationTests(unittest.TestCase):
    def reader(self, request, control):
        # Real admitted fixtures precede the missing-feature guard. Historical
        # unresolved material is not an executable request compatibility fixture.
        RuntimeEffectRequest.__post_init__(request)
        self.assertTrue(runtime_effect_observation_fingerprint(control))
        self.assertEqual(control.effect_id, request.effect_id)
        self.assertEqual(control.request_fingerprint,
            runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)))
        reader = getattr(language("secret_installation"), "secret_installation_receipt_for_observation", None)
        self.assertIsNotNone(reader, "missing actual-observation secret installation receipt reader")
        return reader

    def assert_refusal(self, invoke):
        with self.assertRaises(RuntimeEffectContractError) as caught:
            invoke()
        self.assertLessEqual(len(str(caught.exception)), 256)
        self.assertNotIn("private-canary", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_direct_selected_configuration_requires_its_own_receipt(self):
        # Empty configuration selections remain invalid; never relax the public
        # constructor merely to manufacture a receipt-reader fixture.
        with self.assertRaises(ValueError):
            ConfigurationInstanceSelection(())
        request = request_value()
        evidence = receipt_evidence(request)
        complete = RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)
        self.assertIsNotNone(secret_installation_receipt_for_result(request, complete))
        missing = replace(complete, evidence={key: value for key, value in evidence.items() if key != CONFIGURATION})
        self.assert_refusal(lambda: secret_installation_receipt_for_result(request, missing))

    def test_direct_unsuccessful_selected_consumer_refuses_configuration_only_success_claim(self):
        request = request_value()
        evidence = receipt_evidence(request)
        for kind in (EffectResultKind.FAILED, EffectResultKind.UNCERTAIN, EffectResultKind.UNSUPPORTED):
            with self.subTest(kind=kind):
                result = RuntimeEffectResult(request.effect_id, kind,
                    evidence={CONFIGURATION: evidence[CONFIGURATION]},
                    failure=RuntimeEffectFailure("fixture.unverified", "Installation was not verified."))
                self.assert_refusal(lambda: secret_installation_receipt_for_result(request, result))

    def test_observed_success_preserves_actual_envelope_and_distinct_selection_domains(self):
        for configuration in (False, True):
            for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
                for disposition in ("created", "reused", "replaced"):
                    with self.subTest(configuration=configuration, operation=type(operation).__name__, disposition=disposition):
                        request = request_value(configuration=configuration, operation=operation)
                        evidence = receipt_evidence(request, disposition)
                        observation = observed(request, evidence)
                        before = observation.descriptor(), runtime_effect_observation_fingerprint(observation)
                        reader = self.reader(request, observation)
                        expected = SecretInstallationReceipt(
                            runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)),
                            secret_material_selection_fingerprint(request),
                            SecretInstallationDisposition(disposition),
                            None if disposition == "created" else "a" * 64,
                            ("a" if disposition == "reused" else "b") * 64,
                        )
                        self.assertEqual(reader(request, observation), expected)
                        self.assertEqual((observation.descriptor(), runtime_effect_observation_fingerprint(observation)), before)
                        self.assertIs(getattr(core, "secret_installation_receipt_for_observation", None), reader)
                        if configuration:
                            self.assertNotEqual(evidence[CONFIGURATION]["selection_fingerprint"],
                                evidence[INSTALLATION]["selection_fingerprint"])

    def test_observed_missing_and_foreign_original_commitments_refuse(self):
        request = request_value()
        evidence = receipt_evidence(request)
        control = observed(request, evidence)
        reader = self.reader(request, control)
        candidates = [("effect", replace(control, effect_id="foreign-effect")),
            ("envelope-request", replace(control, request_fingerprint="f" * 64))]
        for key in (RESOLUTION, INSTALLATION, CONFIGURATION):
            candidates.append(("missing-" + key, observed(request,
                {name: value for name, value in evidence.items() if name != key})))
            for field in ("request_fingerprint", "selection_fingerprint"):
                changed = deepcopy(evidence)
                changed[key][field] = "e" * 64
                candidates.append((key + field, observed(request, changed)))
        for label, candidate in candidates:
            with self.subTest(case=label):
                self.assert_refusal(lambda: reader(request, candidate))
        other = request_with_version("version-other")
        self.assert_refusal(lambda: reader(other, control))

    def test_observed_configuration_receipt_and_incarnation_cannot_be_omitted_or_changed(self):
        request = request_value()
        evidence = receipt_evidence(request)
        control = observed(request, evidence)
        reader = self.reader(request, control)
        self.assertIsNotNone(reader(request, control))
        self.assert_refusal(lambda: reader(request, observed(request,
            {key: value for key, value in evidence.items() if key != CONFIGURATION})))
        for changes in (
            {"current_realization_fingerprint": "c" * 64},
            {"prior_realization_fingerprint": "d" * 64},
            {"disposition": "created", "prior_realization_fingerprint": None},
        ):
            changed = deepcopy(evidence)
            changed[CONFIGURATION].update(changes)
            with self.subTest(changes=tuple(changes)):
                self.assert_refusal(lambda: reader(request, observed(request, changed)))

    def test_actual_unsuccessful_variants_cannot_claim_any_partial_success_profile(self):
        request = request_value()
        evidence = receipt_evidence(request)
        reader = self.reader(request, observed(request, evidence))
        for variant in (RuntimeEffectObservedFailed, RuntimeEffectObservedAbsent,
                RuntimeEffectObservedConflict, RuntimeEffectObservedIndeterminate, RuntimeEffectObserverUnsupported):
            with self.subTest(variant=variant.__name__):
                candidate = observed(request, {"reason": "unverified"}, variant)
                self.assertIsNone(reader(request, candidate))
                for key in (RESOLUTION, INSTALLATION, CONFIGURATION):
                    with self.subTest(profile=key):
                        self.assert_refusal(lambda: reader(request, observed(request,
                            {key: evidence[key]}, variant)))

    def test_secret_free_admitted_requests_and_nonconsumer_roles_remain_distinct(self):
        selected = request_value(configuration=False)
        evidence = receipt_evidence(selected)
        for operation in (StartNode(NodeTarget("api")), StartRuntime(RuntimeTarget("docker"))):
            with self.subTest(operation=type(operation).__name__):
                request = request_value(configuration=False, secret=False, operation=operation)
                control = observed(request, {"reason": "ordinary-history"})
                reader = self.reader(request, control)
                self.assertIsNone(reader(request, control))
                self.assert_refusal(lambda: reader(request, observed(request, evidence)))
        nonconsumer = request_value(configuration=False, operation=StartRuntime(RuntimeTarget("docker")))
        control = observed(nonconsumer, {"reason": "operational-only"})
        reader = self.reader(nonconsumer, control)
        self.assertIsNone(reader(nonconsumer, control))
        self.assert_refusal(lambda: reader(nonconsumer, observed(nonconsumer, evidence)))

    def test_exact_observation_and_closed_profile_validation_rejects_forgery_safely(self):
        request = request_value()
        evidence = receipt_evidence(request)
        control = observed(request, evidence)
        reader = self.reader(request, control)
        subclass = type("DerivedObservation", (RuntimeEffectObservedSucceeded,), {})
        derived = subclass(control.effect_id, control.request_fingerprint, control.evidence)
        candidates = (derived, object(), forged(control, evidence={}),
            forged(control, request_fingerprint=7))
        for candidate in candidates:
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_refusal(lambda: reader(request, candidate))
        for candidate in (object(), forged(request, subclass=True), forged(request, products=())):
            with self.subTest(request=type(candidate).__name__):
                self.assert_refusal(lambda: reader(candidate, control))
        for key in (RESOLUTION, INSTALLATION, CONFIGURATION):
            for changes in ({"profile": "wrong-profile"}, {"extra": "private-canary"}):
                changed = deepcopy(evidence)
                changed[key].update(changes)
                with self.subTest(profile=key, change=tuple(changes)):
                    self.assert_refusal(lambda: reader(request, observed(request, changed)))


if __name__ == "__main__":
    unittest.main()
