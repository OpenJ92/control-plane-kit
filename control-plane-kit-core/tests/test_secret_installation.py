"""#1974 correlated assertions, never substitute provider or recipient proof."""
from dataclasses import replace
import unittest

from control_plane_kit_core.runtime_effects import RuntimeEffectContractError, EffectResultKind, RuntimeEffectFailure
from control_plane_kit_core.secrets import SecretFileDelivery
from tests.test_configuration_installation import receipt_result
from tests.test_secret_selections import language, request_with_version


def result_with_receipts(request):
    m = language("secret_installation")
    result = receipt_result(request)
    configuration = result.evidence["configuration_installation_receipt"]
    correlation = {
        "request_fingerprint": configuration["request_fingerprint"],
        "selection_fingerprint": m.secret_material_selection_fingerprint(request),
    }
    return replace(result, evidence={**result.evidence,
        "secret_resolution_receipt": {"profile": "secret-resolution-receipt.v1", **correlation},
        "secret_installation_receipt": {**configuration,
            "profile": "secret-installation-receipt.v1", **correlation}})


class SecretInstallationTests(unittest.TestCase):
    def test_complete_evidence_correlates_original_request_and_both_profiles(self):
        m = language("secret_installation")
        request = request_with_version()
        result = result_with_receipts(request)
        receipt = m.secret_installation_receipt_for_result(request, result)
        self.assertEqual(receipt.current_realization_fingerprint, "b" * 64)
        self.assertEqual(receipt.prior_realization_fingerprint, "a" * 64)
        self.assertNotIn("prior_version", receipt.descriptor())
        codec = m.SecretInstallationReceiptCodec()
        self.assertEqual(codec.decode_canonical_bytes(codec.encode_canonical_bytes(receipt)), receipt)
        with self.assertRaises(RuntimeEffectContractError):
            m.secret_installation_receipt_for_result(request_with_version("version-2"), result)

    def test_success_without_resolution_or_installation_is_not_admitted(self):
        m = language("secret_installation")
        request = request_with_version()
        result = result_with_receipts(request)
        for key in ("secret_resolution_receipt", "secret_installation_receipt"):
            partial = replace(result, evidence={k: v for k, v in result.evidence.items() if k != key})
            with self.subTest(key=key), self.assertRaises(RuntimeEffectContractError):
                m.secret_installation_receipt_for_result(request, partial)

    def test_individually_valid_but_contradictory_incarnations_refuse(self):
        m = language("secret_installation")
        request = request_with_version()
        result = result_with_receipts(request)
        for changes in ({"current_realization_fingerprint": "c" * 64},
                        {"disposition": "created", "prior_realization_fingerprint": None}):
            receipt = {**result.evidence["secret_installation_receipt"], **changes}
            m.SecretInstallationReceiptCodec().decode(receipt)
            contradictory = replace(result, evidence={**result.evidence, "secret_installation_receipt": receipt})
            with self.assertRaises(RuntimeEffectContractError):
                m.secret_installation_receipt_for_result(request, contradictory)

    def test_receipts_are_bounded_closed_and_cannot_contain_secret_values(self):
        m = language("secret_installation")
        request = request_with_version()
        document = result_with_receipts(request).evidence["secret_installation_receipt"]
        codec = m.SecretInstallationReceiptCodec()
        for bad in ({**document, "value": "private-canary"},
                    {**document, "selection_fingerprint": "private-canary"}):
            with self.assertRaises(ValueError) as caught:
                codec.decode(bad)
            self.assertNotIn("private-canary", str(caught.exception))
        with self.assertRaises(ValueError):
            codec.decode_canonical_bytes(b" " * 1025)

    def test_secret_commitment_covers_every_shared_key_target_and_normalizes_order(self):
        m = language("secret_installation")
        request = request_with_version()
        material = request.products[0]
        environment = material.product.runtime_contract.secret_deliveries[0]
        file = SecretFileDelivery("/run/secrets/key", environment.reference, environment.intent)

        def selecting(deliveries):
            return replace(request, products=(replace(material, product=replace(material.product,
                runtime_contract=replace(material.product.runtime_contract, secret_deliveries=deliveries))),))

        full = selecting((environment, file))
        fingerprint = m.secret_material_selection_fingerprint(full)
        self.assertEqual(fingerprint, m.secret_material_selection_fingerprint(selecting((file, environment))))
        self.assertNotEqual(fingerprint, m.secret_material_selection_fingerprint(selecting((environment,))))
        self.assertNotEqual(fingerprint, m.secret_material_selection_fingerprint(
            selecting((environment, replace(file, target_path="/run/secrets/other")))))

    def test_wrong_effect_and_failed_or_uncertain_claims_do_not_prove_installation(self):
        m = language("secret_installation")
        request = request_with_version()
        result = result_with_receipts(request)
        with self.assertRaises(RuntimeEffectContractError):
            m.secret_installation_receipt_for_result(request, replace(result, effect_id="foreign-event"))
        for kind in (EffectResultKind.FAILED, EffectResultKind.UNCERTAIN):
            failure = RuntimeEffectFailure("secret.unverified", "Secret installation is unverified.")
            unproven = replace(result, kind=kind, evidence={}, failure=failure)
            self.assertIsNone(m.secret_installation_receipt_for_result(request, unproven))
            with self.assertRaises(RuntimeEffectContractError):
                m.secret_installation_receipt_for_result(request,
                    replace(unproven, evidence=result.evidence))


if __name__ == "__main__":
    unittest.main()
