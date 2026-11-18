"""#1974/#1975 3B2: shared receipt laws over original correlation data."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import unittest

import control_plane_kit_core as core
import control_plane_kit_core.secret_installation as installation
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.configuration_installation import ConfigurationInstallationReceiptCodec
from control_plane_kit_core.planning import NodeTarget, StartNode, ReconcileNode, StartRuntime, RuntimeTarget
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservedSucceeded, RuntimeEffectObservedFailed, RuntimeEffectObservedAbsent,
    RuntimeEffectObservedConflict, RuntimeEffectObservedIndeterminate, RuntimeEffectObserverUnsupported,
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint,
    runtime_effect_result_fingerprint, runtime_effect_observation_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectFailure,
    RuntimeEffectKind, RuntimeEffectRequest, RuntimeEffectResult,
)
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery, SecretReferenceEnvironmentDelivery, SecretFileDelivery, SecretReference,
)
from control_plane_kit_core.secret_selections import SecretVersionSelection, UnselectedSecretUse
from tests.test_secret_installation_observation import (
    CONFIGURATION, INSTALLATION, RESOLUTION, forged, observed, receipt_evidence, request_value,
)


INTERFACES = (
    "SecretInstallationCorrelation",
    "secret_installation_correlation_for_request",
    "secret_installation_receipt_for_correlated_result",
    "secret_installation_receipt_for_correlated_observation",
)
UNSUCCESSFUL_RESULTS = (EffectResultKind.FAILED, EffectResultKind.UNCERTAIN, EffectResultKind.UNSUPPORTED)
UNSUCCESSFUL_OBSERVATIONS = (
    RuntimeEffectObservedFailed, RuntimeEffectObservedAbsent, RuntimeEffectObservedConflict,
    RuntimeEffectObservedIndeterminate, RuntimeEffectObserverUnsupported,
)
# Literal public commitment document, independent of the component projection
# being refactored. No product, request descriptor or secret bytes are embedded.
SELECTION_CANONICAL = (
    b'{"deliveries":[{"environment_name":"APP_CONTROL_TOKEN","intent":"application.control-token",'
    b'"kind":"environment","reference_id":"secret://local/workspace-a/app/token"}],'
    b'"node_id":"api","runtime_id":"docker","selections":[{"intent":"application.control-token",'
    b'"reference_id":"secret://local/workspace-a/app/token","version_id":"version-1"}],'
    b'"workspace_id":"workspace-a"}'
)


def with_material(request, deliveries, selections):
    material = request.products[0]
    contract = replace(material.product.runtime_contract, secret_deliveries=deliveries)
    return replace(request, products=(replace(material,
        product=replace(material.product, runtime_contract=contract), secret_selections=selections),))


def two_use_request():
    request = request_value()
    material = request.products[0]
    first = material.product.runtime_contract.secret_deliveries[0]
    second = SecretEnvironmentDelivery("SECOND_TOKEN", SecretReference("secret://local/workspace-a/second"),
        first.intent)
    file = SecretFileDelivery("/run/secrets/control", first.reference, first.intent)
    second_selection = SecretVersionSelection(second.reference, second.intent, "version-2")
    return with_material(request, (file, second, first), (second_selection, *material.secret_selections))


class SecretInstallationCorrelationTests(unittest.TestCase):
    def api(self, request):
        # Establish admitted requests and actual envelope controls before the
        # deliberate missing-capability assertion. Collection imports only
        # existing interfaces; target-red must not be a missing import.
        RuntimeEffectRequest.__post_init__(request)
        intent = runtime_effect_intent_for_request(request)
        self.assertTrue(runtime_effect_intent_fingerprint(intent))
        selected = type(request.operation) in (StartNode, ReconcileNode) and bool(request.products[0].secret_selections)
        evidence = receipt_evidence(request) if selected else {"reason": "ordinary-history"}
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)
        observation = observed(request, evidence)
        direct = installation.secret_installation_receipt_for_result(request, result)
        actual = installation.secret_installation_receipt_for_observation(request, observation)
        self.assertEqual(direct, actual)
        if selected:
            self.assertIsNotNone(direct)
        else:
            self.assertIsNone(direct)
        for name in INTERFACES:
            self.assertIsNotNone(getattr(installation, name, None), "missing 3B2 Core capability: " + name)
            self.assertIs(getattr(core, name, None), getattr(installation, name))
        return installation

    def assert_refusal(self, invoke):
        with self.assertRaises(RuntimeEffectContractError) as caught:
            invoke()
        self.assertLessEqual(len(str(caught.exception)), 256)
        self.assertNotIn("private-canary", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def assert_both_refuse(self, module, context, request, evidence):
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)
        observation = observed(request, evidence)
        # Envelope admission remains independently real; only receipt/context
        # correlation is expected to reject these candidates.
        runtime_effect_result_fingerprint(result)
        runtime_effect_observation_fingerprint(observation)
        self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_result(context, result))
        self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_observation(context, observation))

    def test_context_projects_complete_actual_material_without_claiming_origin(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        intent, material = runtime_effect_intent_for_request(request), request.products[0]
        expected = module.SecretInstallationCorrelation(
            request_fingerprint=runtime_effect_intent_fingerprint(intent), effect_id=request.effect_id,
            kind=request.kind, source=intent.source, operation=request.operation,
            runtime_id=material.runtime_id, node_id=material.node_id,
            secret_deliveries=material.product.runtime_contract.secret_deliveries,
            secret_selections=material.secret_selections, configuration_instances=request.configuration_instances,
        )
        self.assertEqual(context, expected)
        self.assertIs(type(context), module.SecretInstallationCorrelation)
        self.assertEqual(len(context.configuration_instances.instances), 2)
        self.assertNotIn(material.secret_selections[0].reference.reference_id, repr(context))
        self.assertNotIn(material.secret_selections[0].version_id, repr(context))
        with self.assertRaises(FrozenInstanceError):
            context.node_id = "foreign"
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=receipt_evidence(request))
        # Supplied algebraic context is usable without a product/request shell.
        # Database provenance is an Operations obligation, not certified here.
        self.assertEqual(module.secret_installation_receipt_for_correlated_result(expected, result),
            installation.secret_installation_receipt_for_result(request, result))

    def test_existing_material_commitment_domain_and_bytes_are_preserved(self):
        request = request_value()
        expected = sha256(b"control-plane-kit.secret-material-selection.v1\0" + SELECTION_CANONICAL).hexdigest()
        self.assertEqual(installation.secret_material_selection_fingerprint(request), expected)
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        evidence = receipt_evidence(request)
        for key in (RESOLUTION, INSTALLATION):
            evidence[key]["selection_fingerprint"] = expected
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)
        receipt = module.secret_installation_receipt_for_correlated_result(context, result)
        self.assertEqual(receipt.selection_fingerprint, expected)
        self.assertEqual(module.secret_installation_receipt_for_correlated_observation(
            context, observed(request, evidence)), receipt)

    def test_direct_reader_parity_preserves_actual_request_and_result(self):
        for configuration in (False, True):
            for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
                request = request_value(configuration=configuration, operation=operation)
                module = self.api(request)
                context = module.secret_installation_correlation_for_request(request)
                for disposition in ("created", "reused", "replaced"):
                    with self.subTest(configuration=configuration, operation=type(operation).__name__, disposition=disposition):
                        result = RuntimeEffectResult.succeeded(request.effect_id,
                            evidence=receipt_evidence(request, disposition))
                        before = deepcopy(request.descriptor()), deepcopy(result.descriptor()), runtime_effect_result_fingerprint(result)
                        expected = installation.secret_installation_receipt_for_result(request, result)
                        self.assertEqual(module.secret_installation_receipt_for_correlated_result(context, result), expected)
                        self.assertEqual((request.descriptor(), result.descriptor(), runtime_effect_result_fingerprint(result)), before)

    def test_observation_reader_parity_preserves_actual_observation_variant(self):
        for configuration in (False, True):
            for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
                request = request_value(configuration=configuration, operation=operation)
                module = self.api(request)
                context = module.secret_installation_correlation_for_request(request)
                for disposition in ("created", "reused", "replaced"):
                    with self.subTest(configuration=configuration, operation=type(operation).__name__, disposition=disposition):
                        observation = observed(request, receipt_evidence(request, disposition))
                        before = deepcopy(observation.descriptor()), runtime_effect_observation_fingerprint(observation)
                        expected = installation.secret_installation_receipt_for_observation(request, observation)
                        self.assertEqual(module.secret_installation_receipt_for_correlated_observation(context, observation), expected)
                        self.assertIs(type(observation), RuntimeEffectObservedSucceeded)
                        self.assertEqual((observation.descriptor(), runtime_effect_observation_fingerprint(observation)), before)

    def test_empty_and_reference_only_nonconsumers_preserve_all_envelope_results(self):
        selected = request_value()
        delivered = selected.products[0].product.runtime_contract.secret_deliveries[0]
        reference_only = SecretReferenceEnvironmentDelivery("TOKEN_REFERENCE", delivered.reference)
        for configuration in (False, True):
            for deliveries in ((), (reference_only,)):
                request = with_material(request_value(configuration=configuration, secret=False), deliveries, ())
                module = self.api(request)
                context = module.secret_installation_correlation_for_request(request)
                self.assertEqual(context.secret_deliveries, deliveries)
                self.assertEqual(context.secret_selections, ())
                for kind in (EffectResultKind.SUCCEEDED, *UNSUCCESSFUL_RESULTS):
                    with self.subTest(configuration=configuration, reference_only=bool(deliveries), result=kind):
                        failure = None if kind is EffectResultKind.SUCCEEDED else RuntimeEffectFailure("fixture.unverified", "Unverified.")
                        result = RuntimeEffectResult(request.effect_id, kind, evidence={"reason": "no-consumer"}, failure=failure)
                        self.assertIsNone(installation.secret_installation_receipt_for_result(request, result))
                        self.assertIsNone(module.secret_installation_receipt_for_correlated_result(context, result))
                for variant in (RuntimeEffectObservedSucceeded, *UNSUCCESSFUL_OBSERVATIONS):
                    with self.subTest(configuration=configuration, reference_only=bool(deliveries), observation=variant.__name__):
                        observation = observed(request, {"reason": "no-consumer"}, variant)
                        self.assertIsNone(installation.secret_installation_receipt_for_observation(request, observation))
                        self.assertIsNone(module.secret_installation_receipt_for_correlated_observation(context, observation))
                # With no resolving consumer, the secret reader does not own
                # a standalone configuration claim. Preserve that distinction;
                # returning None is not accepting it as installation proof.
                configuration_only = {CONFIGURATION: receipt_evidence(selected)[CONFIGURATION]}
                result = RuntimeEffectResult(request.effect_id, EffectResultKind.UNCERTAIN,
                    evidence=configuration_only, failure=RuntimeEffectFailure("fixture.unverified", "Unverified."))
                observation = observed(request, configuration_only, RuntimeEffectObservedIndeterminate)
                self.assertIsNone(installation.secret_installation_receipt_for_result(request, result))
                self.assertIsNone(module.secret_installation_receipt_for_correlated_result(context, result))
                self.assertIsNone(installation.secret_installation_receipt_for_observation(request, observation))
                self.assertIsNone(module.secret_installation_receipt_for_correlated_observation(context, observation))
                self.assert_both_refuse(module, context, request, receipt_evidence(selected))

    def test_non_node_requests_keep_full_reader_behavior_without_node_context(self):
        for secret in (False, True):
            request = request_value(configuration=False, secret=secret, operation=StartRuntime(RuntimeTarget("docker")))
            module = self.api(request)
            self.assert_refusal(lambda: module.secret_installation_correlation_for_request(request))
            for kind in (EffectResultKind.SUCCEEDED, *UNSUCCESSFUL_RESULTS):
                failure = None if kind is EffectResultKind.SUCCEEDED else RuntimeEffectFailure("fixture.unverified", "Unverified.")
                result = RuntimeEffectResult(request.effect_id, kind, evidence={"reason": "non-node"}, failure=failure)
                self.assertIsNone(installation.secret_installation_receipt_for_result(request, result))
            for variant in (RuntimeEffectObservedSucceeded, *UNSUCCESSFUL_OBSERVATIONS):
                self.assertIsNone(installation.secret_installation_receipt_for_observation(
                    request, observed(request, {"reason": "non-node"}, variant)))
            evidence = receipt_evidence(request_value(configuration=False))
            self.assert_refusal(lambda: installation.secret_installation_receipt_for_result(
                request, RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)))
            self.assert_refusal(lambda: installation.secret_installation_receipt_for_observation(request, observed(request, evidence)))

    def test_missing_foreign_and_partial_receipts_refuse_for_both_readers(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        evidence = receipt_evidence(request)
        for key in (RESOLUTION, INSTALLATION, CONFIGURATION):
            with self.subTest(profile=key, case="missing"):
                self.assert_both_refuse(module, context, request, {name: value for name, value in evidence.items() if name != key})
            for field in ("request_fingerprint", "selection_fingerprint"):
                with self.subTest(profile=key, field=field):
                    changed = deepcopy(evidence)
                    changed[key][field] = "f" * 64
                    self.assert_both_refuse(module, context, request, changed)
        other_context = replace(context, request_fingerprint="e" * 64)
        self.assert_both_refuse(module, other_context, request, evidence)

    def test_unsuccessful_variants_preserve_no_proof_and_refuse_success_claims(self):
        for configuration in (False, True):
            request = request_value(configuration=configuration)
            module = self.api(request)
            context = module.secret_installation_correlation_for_request(request)
            claims = receipt_evidence(request)
            for kind in UNSUCCESSFUL_RESULTS:
                result = RuntimeEffectResult(request.effect_id, kind, evidence={},
                    failure=RuntimeEffectFailure("fixture.unverified", "Unverified."))
                with self.subTest(configuration=configuration, result=kind):
                    self.assertIsNone(installation.secret_installation_receipt_for_result(request, result))
                    self.assertIsNone(module.secret_installation_receipt_for_correlated_result(context, result))
                    for key, value in claims.items():
                        with self.subTest(profile=key):
                            self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_result(
                                context, replace(result, evidence={key: value})))
            for variant in UNSUCCESSFUL_OBSERVATIONS:
                with self.subTest(configuration=configuration, observation=variant.__name__):
                    observation = observed(request, {"reason": "unverified"}, variant)
                    self.assertIsNone(installation.secret_installation_receipt_for_observation(request, observation))
                    self.assertIsNone(module.secret_installation_receipt_for_correlated_observation(context, observation))
                    for key, value in claims.items():
                        with self.subTest(profile=key):
                            self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_observation(
                                context, observed(request, {key: value}, variant)))

    def test_closed_profiles_and_cross_profile_realizations_cannot_diverge(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        evidence = receipt_evidence(request)
        for key in (RESOLUTION, INSTALLATION, CONFIGURATION):
            for changes in ({"profile": "wrong-profile"}, {"extra": "private-canary"}):
                with self.subTest(profile=key, changes=tuple(changes)):
                    candidate = deepcopy(evidence)
                    candidate[key].update(changes)
                    self.assert_both_refuse(module, context, request, candidate)
        for changes in ({"current_realization_fingerprint": "c" * 64},
                        {"prior_realization_fingerprint": "d" * 64},
                        {"disposition": "created", "prior_realization_fingerprint": None}):
            candidate = deepcopy(evidence)
            candidate[CONFIGURATION].update(changes)
            ConfigurationInstallationReceiptCodec().decode(candidate[CONFIGURATION])
            with self.subTest(realization_change=tuple(changes)):
                self.assert_both_refuse(module, context, request, candidate)
        ordinary = request_value(configuration=False)
        ordinary_context = module.secret_installation_correlation_for_request(ordinary)
        extra = {**receipt_evidence(ordinary), CONFIGURATION: evidence[CONFIGURATION]}
        self.assert_both_refuse(module, ordinary_context, ordinary, extra)

    def test_context_scope_kind_and_complete_configuration_selection_are_bound(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        for changes in (
            {"source": replace(context.source, workspace_id="foreign-workspace")},
            {"operation": StartNode(NodeTarget("foreign-node"))},
            {"node_id": "foreign-node"}, {"runtime_id": "foreign-runtime"},
            {"kind": RuntimeEffectKind.REALIZE_ACTIVITY}, {"kind": context.kind.value},
            {"configuration_instances": None}, {"operation": StartRuntime(RuntimeTarget("docker"))},
            {"effect_id": ""}, {"request_fingerprint": "private-canary"},
        ):
            with self.subTest(changes=tuple(changes)):
                self.assert_refusal(lambda: replace(context, **changes))
        refs = context.configuration_instances.instances
        for selection in (ConfigurationInstanceSelection((refs[0],)),
                ConfigurationInstanceSelection((replace(refs[0], allocation_id="foreign-allocation"), refs[1]))):
            # These are lawful supplied correlations, but the actual original
            # receipt commits to the full original selection, not this one.
            changed = replace(context, configuration_instances=selection)
            self.assert_both_refuse(module, changed, request, receipt_evidence(request))

    def test_selected_material_requires_exact_complete_canonical_version_coverage(self):
        request = two_use_request()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        entries = context.secret_selections
        self.assertEqual(len(entries), 2)
        other = SecretVersionSelection(SecretReference("secret://local/workspace-a/unrelated"), entries[0].intent, "v1")
        for selections in ((), entries[:1], (*entries, entries[0]), (*entries, other),
                (UnselectedSecretUse(entries[0].reference, entries[0].intent), entries[1]),
                tuple(reversed(entries)), list(entries)):
            with self.subTest(selection_type=type(selections).__name__, count=len(selections)):
                self.assert_refusal(lambda: replace(context, secret_selections=selections))
        # Full material retains its established normalizing authoring path.
        permuted = with_material(request, tuple(reversed(context.secret_deliveries)), tuple(reversed(entries)))
        self.assertEqual(module.secret_installation_correlation_for_request(permuted), context)

    def test_commitment_covers_targets_and_preserves_existing_delivery_multiplicity(self):
        request = two_use_request()
        material = request.products[0]
        deliveries = material.product.runtime_contract.secret_deliveries
        file = next(value for value in deliveries if type(value) is SecretFileDelivery)
        # Prove this existing-law fixture before any missing-interface guard.
        duplicated = with_material(request, (*deliveries, file), material.secret_selections)
        RuntimeEffectRequest.__post_init__(duplicated)
        duplicate_result = RuntimeEffectResult.succeeded(duplicated.effect_id, evidence=receipt_evidence(duplicated))
        duplicate_receipt = installation.secret_installation_receipt_for_result(duplicated, duplicate_result)
        self.assertIsNotNone(duplicate_receipt)
        self.assertNotEqual(installation.secret_material_selection_fingerprint(duplicated),
            installation.secret_material_selection_fingerprint(request))
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        evidence = receipt_evidence(request)
        context_files = tuple(value for value in context.secret_deliveries if type(value) is SecretFileDelivery)
        self.assertEqual(len(context_files), 1)
        self.assertEqual(context_files[0], file)
        changed = replace(context, secret_deliveries=tuple(
            replace(value, target_path="/run/secrets/other") if type(value) is SecretFileDelivery else value
            for value in context.secret_deliveries))
        changed_files = tuple(value for value in changed.secret_deliveries if type(value) is SecretFileDelivery)
        self.assertEqual(changed_files, (replace(context_files[0], target_path="/run/secrets/other"),))
        self.assertNotEqual(changed, context)
        self.assert_both_refuse(module, changed, request, evidence)
        # The existing product accepts repeated delivery declarations; the
        # correlation must not invent a stricter uniqueness law.
        duplicated_context = module.secret_installation_correlation_for_request(duplicated)
        self.assertEqual(module.secret_installation_receipt_for_correlated_result(duplicated_context, duplicate_result),
            duplicate_receipt)

    def test_nominal_context_and_nested_material_are_revalidated_without_repair(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        delivery, selection = context.secret_deliveries[0], context.secret_selections[0]
        candidates = (
            object(), forged(context, subclass=True), forged(context, source=object()),
            forged(context, secret_deliveries=({"kind": "unknown", "value": "private-canary"},)),
            forged(context, secret_deliveries=(forged(delivery, subclass=True),)),
            forged(context, secret_selections=(forged(selection, version_id="private-canary\n"),)),
            forged(context, secret_selections=(forged(selection, subclass=True),)),
            forged(context, operation=forged(context.operation, subclass=True)),
        )
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=receipt_evidence(request))
        observation = observed(request, receipt_evidence(request))
        for candidate in candidates:
            with self.subTest(candidate=type(candidate).__name__):
                retained_fields = ("source", "operation", "secret_deliveries", "secret_selections")
                before = (tuple(getattr(candidate, name) for name in retained_fields)
                    if type(candidate) is type(context) else ())
                self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_result(candidate, result))
                self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_observation(candidate, observation))
                for name, original in zip(retained_fields, before):
                    self.assertIs(getattr(candidate, name), original, "refusal must not repair supplied context")
        self.assertEqual(context, module.secret_installation_correlation_for_request(request))
        self.assertIsNotNone(module.secret_installation_receipt_for_correlated_result(context, result))

    def test_actual_envelopes_and_factory_inputs_cannot_bypass_exact_validation(self):
        request = request_value()
        module = self.api(request)
        context = module.secret_installation_correlation_for_request(request)
        result = RuntimeEffectResult.succeeded(request.effect_id, evidence=receipt_evidence(request))
        observation = observed(request, receipt_evidence(request))
        for candidate in (object(), forged(result, subclass=True), forged(result, evidence=object()),
                          replace(result, effect_id="foreign-effect")):
            self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_result(context, candidate))
        for candidate in (object(), forged(observation, subclass=True), forged(observation, evidence={}),
                          replace(observation, effect_id="foreign-effect"),
                          replace(observation, request_fingerprint="f" * 64)):
            self.assert_refusal(lambda: module.secret_installation_receipt_for_correlated_observation(context, candidate))
        for candidate in (object(), forged(request, subclass=True), forged(request, products=()),
                          forged(request, products=(request.products[0], request.products[0]))):
            self.assert_refusal(lambda: module.secret_installation_correlation_for_request(candidate))


if __name__ == "__main__":
    unittest.main()
