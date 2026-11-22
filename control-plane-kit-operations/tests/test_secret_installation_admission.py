"""Operations admission of Core-owned receipts; no provider conformance claim."""
from dataclasses import replace
import unittest

from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectFailure, RuntimeEffectResult,
)
from control_plane_kit_core.secret_installation import (
    SecretInstallationDisposition, SecretInstallationReceipt,
    SecretResolutionReceipt, secret_installation_receipt_for_result,
    secret_material_selection_fingerprint,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import RuntimeInterpreterDispatcher
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from tests.test_secret_version_dispatch import (
    VersionAuthorizer, registration, selected_context,
)


def receipt_result(request):
    fingerprint = runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request))
    selection = secret_material_selection_fingerprint(request)
    resolution = SecretResolutionReceipt(fingerprint, selection)
    installation = SecretInstallationReceipt(fingerprint, selection,
        SecretInstallationDisposition.CREATED, None, "b" * 64)
    return RuntimeEffectResult.succeeded(request.effect_id, evidence={
        "secret_resolution_receipt": resolution.descriptor(),
        "secret_installation_receipt": installation.descriptor(),
    })


class ReceiptAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request):
        self.calls.append(("execute", request, None))
        return self.result

    def execute_with_authority(self, request, authority):
        self.calls.append(("execute_with_authority", request, authority))
        return self.result


class SecretInstallationAdmissionTests(unittest.TestCase):
    def dispatch(self, context, request, result):
        adapter = ReceiptAdapter(result)
        authorizer = VersionAuthorizer()
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registration(
            adapter, exact_version_execute=True, exact_version_execute_with_authority=True)},
            secret_use_authorizer=authorizer)
        returned = dispatcher.execute_runtime(context, request)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(authorizer.version_commands), 1)
        arm, admitted, authority = adapter.calls[0]
        self.assertEqual(arm, "execute_with_authority" if request.authority_ref else "execute")
        self.assertEqual(runtime_effect_intent_for_request(admitted),
            runtime_effect_intent_for_request(request))
        self.assertEqual(admitted.secret_resolution_grants[0].selection,
            request.products[0].secret_selections[0])
        self.assertEqual(authority is not None, request.authority_ref is not None)
        return returned

    def assert_admission_uncertain(self, result, request):
        self.assertIs(result.kind, EffectResultKind.UNCERTAIN)
        self.assertEqual(result.effect_id, request.effect_id)
        self.assertEqual(result.failure.code, "runtime.provider-result-unknown")
        self.assertEqual(result.failure.details,
            {"boundary": "secret-installation", "reason": "receipt-unavailable"})
        self.assertNotIn("secret_resolution_receipt", result.evidence)
        self.assertNotIn("secret_installation_receipt", result.evidence)
        self.assertNotIn("private-canary", repr(result.failure))

    def test_both_dispatch_arms_preserve_complete_original_receipt(self):
        for with_authority in (False, True):
            with self.subTest(with_authority=with_authority):
                context = selected_context(with_authority=with_authority)
                request = runtime_effect_request_for_context(context)
                result = receipt_result(request)
                self.assertIsNotNone(secret_installation_receipt_for_result(request, result))
                returned = self.dispatch(context, request, result)
                self.assertIs(returned, result)
                self.assertEqual(returned.evidence, result.evidence)

    def test_both_dispatch_arms_make_missing_or_foreign_success_proof_uncertain(self):
        for with_authority in (False, True):
            context = selected_context(with_authority=with_authority)
            request = runtime_effect_request_for_context(context)
            complete = receipt_result(request)
            self.assertIsNotNone(secret_installation_receipt_for_result(request, complete))
            foreign = {**complete.evidence["secret_installation_receipt"],
                "selection_fingerprint": "f" * 64}
            malformed = {**complete.evidence["secret_installation_receipt"],
                "secret_value": "private-canary"}
            candidates = (
                RuntimeEffectResult.succeeded(request.effect_id),
                replace(complete, evidence={**complete.evidence, "secret_installation_receipt": foreign}),
                replace(complete, evidence={**complete.evidence, "secret_installation_receipt": malformed}),
            )
            for index, candidate in enumerate(candidates):
                with self.subTest(with_authority=with_authority, candidate=index):
                    returned = self.dispatch(context, request, candidate)
                    self.assert_admission_uncertain(returned, request)

    def test_dispatch_rejects_success_profiles_on_failed_and_uncertain_results(self):
        for with_authority in (False, True):
            context = selected_context(with_authority=with_authority)
            request = runtime_effect_request_for_context(context)
            complete = receipt_result(request)
            for kind in (EffectResultKind.FAILED, EffectResultKind.UNCERTAIN):
                control = RuntimeEffectResult(request.effect_id, kind,
                    failure=RuntimeEffectFailure("fixture.unverified", "Installation is unverified."))
                self.assertIsNone(secret_installation_receipt_for_result(request, control))
                self.assertIs(self.dispatch(context, request, control), control)
                for evidence in (complete.evidence,
                        {"secret_resolution_receipt": complete.evidence["secret_resolution_receipt"]}):
                    with self.subTest(with_authority=with_authority, kind=kind, keys=tuple(evidence)):
                        returned = self.dispatch(context, request, replace(control, evidence=evidence))
                        self.assert_admission_uncertain(returned, request)
