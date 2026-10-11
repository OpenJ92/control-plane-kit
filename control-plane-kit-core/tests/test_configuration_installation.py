"""#1964 receipt syntax and correlation; provider truth remains external."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import json
import unittest

import rfc8785

from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.configuration_invocation import (
    configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
    configuration_invocation_completion_for_result,
)
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartNode
from control_plane_kit_core.runtime_effect_observation import runtime_effect_result_fingerprint
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectFailure, RuntimeEffectResult,
)
from tests import test_configuration_effect_contract as request_fixtures
from tests import test_configuration_invocation as invocation_fixtures

MODULE = "control_plane_kit_core.configuration_installation"
PROFILE = "configuration-installation-receipt.v1"
KEY = "configuration_installation_receipt"


def language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1964 configuration installation language") from error
    for name in ("ConfigurationInstallationReceipt", "ConfigurationInstallationDisposition",
                 "ConfigurationInstallationReceiptCodec", "configuration_installation_receipt_for_result"):
        if not hasattr(module, name):
            raise AssertionError(f"missing #1964 public behavior: {name}")
    return module


def payload(request, disposition="replaced"):
    context = configuration_invocation_correlation_for_request(request)
    return dict(profile=PROFILE, request_fingerprint=context.request_fingerprint,
        selection_fingerprint=configuration_invocation_selection_fingerprint(context.selection),
        disposition=disposition,
        prior_realization_fingerprint=None if disposition == "created" else "a" * 64,
        current_realization_fingerprint=("a" if disposition == "reused" else "b") * 64)


def receipt_result(request, disposition="replaced"):
    original = invocation_fixtures.result(request)
    return replace(original, evidence={**original.evidence, KEY: payload(request, disposition)})


class ConfigurationInstallationReceiptTests(unittest.TestCase):
    def test_closed_value_roundtrips_canonically_and_is_frozen_detached(self):
        m = language()
        codec = m.ConfigurationInstallationReceiptCodec()
        request = request_fixtures.selected_request()
        self.assertEqual({item.value for item in m.ConfigurationInstallationDisposition},
            {"created", "reused", "replaced"})
        for disposition in m.ConfigurationInstallationDisposition:
            document = payload(request, disposition.value)
            arguments = {key: value for key, value in document.items() if key != "profile"}
            arguments["disposition"] = disposition
            value = m.ConfigurationInstallationReceipt(**arguments)
            self.assertEqual(value.descriptor(), document)
            self.assertEqual(codec.encode(value), document)
            self.assertEqual(codec.decode(document), value)
            raw = rfc8785.dumps(document)
            self.assertEqual(codec.encode_canonical_bytes(value), raw)
            self.assertEqual(codec.decode_canonical_bytes(raw), value)
            self.assertEqual(codec.encode_canonical_bytes(codec.decode(dict(reversed(tuple(document.items()))))), raw)
            with self.assertRaises(FrozenInstanceError):
                value.current_realization_fingerprint = "c" * 64
            copied = value.descriptor()
            copied["current_realization_fingerprint"] = "c" * 64
            self.assertEqual(value.descriptor(), document)

    def test_generation_laws_reject_every_invalid_prior_current_relationship(self):
        m = language()
        document = payload(request_fixtures.selected_request())
        for disposition in m.ConfigurationInstallationDisposition:
            for prior in (None, "a" * 64, "b" * 64):
                for current in (None, "a" * 64, "b" * 64):
                    valid = current is not None and (
                        disposition.value == "created" and prior is None
                        or disposition.value == "reused" and prior == current
                        or disposition.value == "replaced" and prior is not None and prior != current)
                    candidate = {**document, "disposition": disposition.value,
                        "prior_realization_fingerprint": prior, "current_realization_fingerprint": current}
                    with self.subTest(disposition=disposition.value, prior=prior, current=current):
                        if valid:
                            self.assertEqual(m.ConfigurationInstallationReceiptCodec().decode(candidate).descriptor(), candidate)
                        else:
                            with self.assertRaises(RuntimeEffectContractError):
                                m.ConfigurationInstallationReceiptCodec().decode(candidate)

    def test_exact_fields_profiles_and_digest_enum_scalars_are_required(self):
        m = language()
        codec = m.ConfigurationInstallationReceiptCodec()
        document = payload(request_fixtures.selected_request())
        malformed = [None, [], {**document, "profile": PROFILE + "x"},
            {**document, "profile": True}, {**document, "provider_id": "private-canary"}]
        malformed += [{key: value for key, value in document.items() if key != missing} for missing in document]
        for field in ("request_fingerprint", "selection_fingerprint",
                      "prior_realization_fingerprint", "current_realization_fingerprint"):
            for bad in (None, True, 64, "", "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n", "private-canary"):
                malformed.append({**document, field: bad})
        malformed += [{**document, "disposition": bad} for bad in (None, True, 1, "", "updated", "REPLACED")]
        for candidate in malformed:
            with self.subTest(candidate=candidate), self.assertRaises(RuntimeEffectContractError):
                codec.decode(candidate)
        value = codec.decode(document)
        for field in ("request_fingerprint", "selection_fingerprint",
                      "prior_realization_fingerprint", "current_realization_fingerprint"):
            with self.subTest(field=field), self.assertRaises(RuntimeEffectContractError):
                replace(value, **{field: "private-canary"})
        with self.assertRaises(RuntimeEffectContractError):
            replace(value, disposition="replaced")

    def test_canonical_bytes_reject_malformed_duplicate_noncanonical_and_over_bound_input(self):
        m = language()
        codec = m.ConfigurationInstallationReceiptCodec()
        document = payload(request_fixtures.selected_request())
        raw = rfc8785.dumps(document)
        malformed = [None, raw.decode(), bytearray(raw), b"", b"{", b"\xff",
            b" " + raw, raw + b"\n", json.dumps(document).encode(), b"x" * 1025,
            raw[:-1] + b',"profile":"' + PROFILE.encode() + b'"}',
            raw.replace(b'"prior_realization_fingerprint":"' + b"a" * 64 + b'"',
                        b'"prior_realization_fingerprint":NaN'),
            raw.replace(b"configuration-installation", b"configuration-\\u0069nstallation")]
        for candidate in malformed:
            with self.subTest(candidate=candidate), self.assertRaises(RuntimeEffectContractError) as caught:
                codec.decode_canonical_bytes(candidate)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)

    def test_reader_correlates_both_operations_and_preserves_complete_result(self):
        m = language()
        for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
            request = request_fixtures.selected_request(operation)
            context = configuration_invocation_correlation_for_request(request)
            for disposition in ("created", "reused", "replaced"):
                value = receipt_result(request, disposition)
                before = deepcopy(value.descriptor())
                fingerprint = runtime_effect_result_fingerprint(value)
                receipt = m.configuration_installation_receipt_for_result(context, value)
                self.assertEqual(receipt.descriptor(), payload(request, disposition))
                self.assertEqual(value.descriptor(), before)
                self.assertEqual(runtime_effect_result_fingerprint(value), fingerprint)
                self.assertEqual(value.observations, (invocation_fixtures.endpoint(),))
                self.assertIsNotNone(configuration_invocation_completion_for_result(context, value))

    def test_absent_receipt_preserves_unprofiled_and_completion_only_history(self):
        m = language()
        request = request_fixtures.selected_request()
        context = configuration_invocation_correlation_for_request(request)
        for kind in (EffectResultKind.SUCCEEDED, EffectResultKind.FAILED,
                     EffectResultKind.UNSUPPORTED, EffectResultKind.UNCERTAIN):
            for completion in (False, True):
                original = invocation_fixtures.result(request, kind=kind, profile=completion)
                before = rfc8785.dumps(original.descriptor())
                self.assertIsNone(m.configuration_installation_receipt_for_result(context, original))
                self.assertEqual(rfc8785.dumps(original.descriptor()), before)
                if completion and kind in (EffectResultKind.SUCCEEDED, EffectResultKind.FAILED):
                    self.assertIsNotNone(configuration_invocation_completion_for_result(context, original))
        for invalid in (None, {}, [], False, "private-canary"):
            value = replace(receipt_result(request), evidence={KEY: invalid})
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeEffectContractError):
                m.configuration_installation_receipt_for_result(context, value)

    def test_present_receipt_requires_success_and_exact_effect_request_selection(self):
        m = language()
        request = request_fixtures.selected_request()
        context = configuration_invocation_correlation_for_request(request)
        value = receipt_result(request)
        candidates = [replace(value, kind=kind, failure=RuntimeEffectFailure("failed", "Failed."))
            for kind in (EffectResultKind.FAILED, EffectResultKind.UNSUPPORTED, EffectResultKind.UNCERTAIN)]
        candidates.append(replace(value, effect_id="another-original-event"))
        for field in ("request_fingerprint", "selection_fingerprint"):
            candidates.append(replace(value, evidence={KEY: {**payload(request), field: "f" * 64}}))
        for candidate in candidates:
            with self.subTest(kind=candidate.kind), self.assertRaises(RuntimeEffectContractError):
                m.configuration_installation_receipt_for_result(context, candidate)

    def test_partial_or_changed_full_selection_cannot_match_receipt(self):
        m = language()
        request = request_fixtures.selected_request()
        context = configuration_invocation_correlation_for_request(request)
        a, b = context.selection.instances
        for selection in (ConfigurationInstanceSelection((a,)),
                ConfigurationInstanceSelection((replace(a, allocation_id="different"), b)),
                ConfigurationInstanceSelection((replace(a, content_digest="e" * 64), b))):
            with self.subTest(selection=selection), self.assertRaises(RuntimeEffectContractError):
                m.configuration_installation_receipt_for_result(replace(context, selection=selection), receipt_result(request))

    def test_full_result_ceiling_applies_before_present_or_absent_receipt(self):
        m = language()
        request = request_fixtures.selected_request()
        context = configuration_invocation_correlation_for_request(request)
        for include in (False, True):
            for size in (8192, 8193):
                value = None
                for count in range(30):
                    evidence = {f"padding-{i:02d}": "x" * 512 for i in range(count)}
                    if include:
                        evidence[KEY] = payload(request)
                    evidence["tail"] = ""
                    seed = RuntimeEffectResult.succeeded(request.effect_id, evidence=evidence)
                    remaining = size - len(rfc8785.dumps(seed.descriptor()))
                    if 0 <= remaining <= 512:
                        evidence["tail"] = "x" * remaining
                        value = replace(seed, evidence=evidence)
                        break
                self.assertIsNotNone(value, "fixture must represent the requested result size")
                self.assertEqual(len(rfc8785.dumps(value.descriptor())), size)
                if size == 8192:
                    self.assertEqual(m.configuration_installation_receipt_for_result(context, value) is not None, include)
                else:
                    with self.assertRaises(RuntimeEffectContractError):
                        m.configuration_installation_receipt_for_result(context, value)

    def test_reader_revalidates_context_and_complete_result_even_before_absence(self):
        m = language()
        request = request_fixtures.selected_request()
        context = configuration_invocation_correlation_for_request(request)
        bad_context = deepcopy(context)
        object.__setattr__(bad_context, "request_fingerprint", "private-canary")
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_installation_receipt_for_result(bad_context, receipt_result(request))
        for field, invalid in (("evidence", {"ordinary": object()}), ("observations", []),
                ("failure", RuntimeEffectFailure("failed", "Failed.")), ("kind", "succeeded")):
            original = invocation_fixtures.result(request, profile=False)
            object.__setattr__(original, field, invalid)
            with self.subTest(field=field), self.assertRaises(RuntimeEffectContractError):
                m.configuration_installation_receipt_for_result(context, original)

    def test_codec_and_descriptor_revalidate_forged_values_with_redacted_errors(self):
        m = language()
        codec = m.ConfigurationInstallationReceiptCodec()
        value = codec.decode(payload(request_fixtures.selected_request()))
        object.__setattr__(value, "current_realization_fingerprint", "private-canary")
        for operation in (lambda: codec.encode(value), lambda: codec.encode_canonical_bytes(value), value.descriptor):
            with self.assertRaises(RuntimeEffectContractError) as caught:
                operation()
            self.assertNotIn("private-canary", str(caught.exception))
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
