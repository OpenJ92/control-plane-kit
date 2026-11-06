"""#1927 C-N01/N02: completion syntax is exact correlation, not authority."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import importlib
import unittest

import rfc8785

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceSelection, ConfigurationInstanceSelectionCodec,
)
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartNode, StopNode
from control_plane_kit_core.probe_intents import (
    EndpointContext, LiteralEndpointMaterial, RuntimeEndpointObservation,
)
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectFailure,
    RuntimeEffectKind, RuntimeEffectResult,
)
from control_plane_kit_core.types import Protocol
from tests.test_configuration_effect_contract import selected_request
from tests.test_runtime_effect_intent import _request


MODULE = "control_plane_kit_core.configuration_invocation"
PROFILE = "configuration-invocation-completion.v1"
KEY = "configuration_invocation_completion"
DOMAIN = b"control-plane-kit.configuration-invocation-selection.v1\x00"


def language():
    try:
        value = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1927 configuration invocation language") from error
    names = (
        "ConfigurationInvocationCompletion", "ConfigurationInvocationCorrelation",
        "configuration_invocation_selection_fingerprint",
        "decode_configuration_invocation_completion",
        "configuration_invocation_correlation_for_request",
        "configuration_invocation_completion_for_result",
    )
    missing = tuple(name for name in names if not hasattr(value, name))
    if missing:
        raise AssertionError(f"missing #1927 public behavior: {missing}")
    return value


def payload(request):
    intent = runtime_effect_intent_for_request(request)
    return {
        "profile": PROFILE,
        "request_fingerprint": runtime_effect_intent_fingerprint(intent),
        "selection_fingerprint": sha256(DOMAIN + ConfigurationInstanceSelectionCodec()
            .encode_canonical_bytes(request.configuration_instances)).hexdigest(),
    }


def endpoint():
    return RuntimeEndpointObservation(
        subject_id="api", socket_name="http", graph_id="graph-desired",
        protocol=Protocol.HTTP, context=EndpointContext.RUNTIME_PRIVATE,
        address=LiteralEndpointMaterial("http://example.invalid:8000"),
    )


def result(request, *, kind=EffectResultKind.SUCCEEDED, profile=True):
    evidence = {"ordinary": {"installed": True}}
    if profile:
        evidence[KEY] = payload(request)
    failure = None if kind is EffectResultKind.SUCCEEDED else RuntimeEffectFailure(
        "configuration.install-failed", "Installation failed.", {"terminal": True})
    return RuntimeEffectResult(request.effect_id, kind, evidence=evidence,
        failure=failure, observations=(endpoint(),))


class ConfigurationInvocationCompletionTests(unittest.TestCase):
    def test_completion_descriptor_is_closed_frozen_and_roundtrips(self):
        m = language()
        expected = payload(selected_request())
        completion = m.ConfigurationInvocationCompletion(
            expected["request_fingerprint"], expected["selection_fingerprint"])
        self.assertEqual(completion.descriptor(), expected)
        self.assertEqual(m.decode_configuration_invocation_completion(expected), completion)
        with self.assertRaises(FrozenInstanceError):
            completion.request_fingerprint = "b" * 64
        copied = completion.descriptor()
        copied["request_fingerprint"] = "b" * 64
        self.assertEqual(completion.descriptor(), expected)

    def test_completion_rejects_unknown_fields_profiles_and_non_digest_scalars(self):
        m = language()
        expected = payload(selected_request())
        malformed = [None, [], {**expected, "profile": "configuration-invocation-completion.v2"},
            {**expected, "safe": True}, {**expected, "effect_id": "invented"}]
        malformed += [{k: v for k, v in expected.items() if k != missing} for missing in expected]
        for field in ("request_fingerprint", "selection_fingerprint"):
            for bad in (None, True, 64, "", "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n"):
                malformed.append({**expected, field: bad})
                arguments = {"request_fingerprint": expected["request_fingerprint"],
                    "selection_fingerprint": expected["selection_fingerprint"], field: bad}
                with self.subTest(field=field, bad=bad), self.assertRaises(RuntimeEffectContractError):
                    m.ConfigurationInvocationCompletion(**arguments)
        for candidate in malformed:
            with self.subTest(candidate=candidate), self.assertRaises(RuntimeEffectContractError):
                m.decode_configuration_invocation_completion(candidate)

    def test_selection_fingerprint_commits_existing_canonical_whole_selection(self):
        m = language()
        selection = selected_request().configuration_instances
        expected = sha256(DOMAIN + ConfigurationInstanceSelectionCodec()
            .encode_canonical_bytes(selection)).hexdigest()
        self.assertEqual(m.configuration_invocation_selection_fingerprint(selection), expected)
        self.assertEqual(m.configuration_invocation_selection_fingerprint(
            ConfigurationInstanceSelection(tuple(reversed(selection.instances)))), expected)
        a, b = selection.instances
        changes = (ConfigurationInstanceSelection((a,)),
            ConfigurationInstanceSelection((replace(a, allocation_id="different"), b)),
            ConfigurationInstanceSelection((replace(a, content_digest="b" * 64), b)),
            ConfigurationInstanceSelection(tuple(replace(ref, runtime_id="other")
                for ref in selection.instances)))
        for changed in changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(m.configuration_invocation_selection_fingerprint(changed), expected)

    def test_real_request_helper_agrees_with_same_source_context_for_both_operations(self):
        m = language()
        contexts = []
        for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
            request = selected_request(operation)
            intent = runtime_effect_intent_for_request(request)
            context = m.ConfigurationInvocationCorrelation(
                request_fingerprint=runtime_effect_intent_fingerprint(intent),
                effect_id=request.effect_id, kind=request.kind, source=intent.source,
                operation=operation, selection=request.configuration_instances)
            self.assertEqual(m.configuration_invocation_correlation_for_request(request), context)
            self.assertEqual(context.selection, request.configuration_instances)
            contexts.append(context)
        self.assertNotEqual(contexts[0].request_fingerprint, contexts[1].request_fingerprint)
        # Nominal syntax cannot prove original operation from an asserted digest.
        self.assertEqual(replace(contexts[0], operation=contexts[1].operation).operation,
            contexts[1].operation)

    def test_context_rejects_unsupported_kind_operation_source_scope_and_scalars(self):
        m = language()
        context = m.configuration_invocation_correlation_for_request(selected_request())
        for change in ({"kind": RuntimeEffectKind.REALIZE_ACTIVITY}, {"kind": "configuration-activity.v1"},
            {"operation": StopNode(NodeTarget("api"))}, {"operation": StartNode(NodeTarget("foreign"))},
            {"source": replace(context.source, workspace_id="foreign")}, {"source": None},
            {"selection": None}, {"request_fingerprint": "not-a-digest"},
            {"effect_id": ""}, {"effect_id": "e" * 513}, {"effect_id": "bad\ud800"}):
            with self.subTest(change=change), self.assertRaises(RuntimeEffectContractError):
                replace(context, **change)
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_invocation_correlation_for_request(_request(complete=False))
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_invocation_correlation_for_request(object())

    def test_terminal_success_and_failure_preserve_complete_ordinary_result(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)
        for kind in (EffectResultKind.SUCCEEDED, EffectResultKind.FAILED):
            value = result(request, kind=kind)
            before = deepcopy(value.descriptor())
            fingerprint = runtime_effect_result_fingerprint(value)
            completion = m.configuration_invocation_completion_for_result(context, value)
            self.assertEqual(completion.descriptor(), payload(request))
            self.assertEqual(value.descriptor(), before)
            self.assertEqual(runtime_effect_result_fingerprint(value), fingerprint)
            self.assertEqual(value.observations, (endpoint(),))
            if kind is EffectResultKind.FAILED:
                self.assertIsNotNone(value.failure)

    def test_absent_profile_is_none_but_present_invalid_is_not_absence(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)
        for kind in EffectResultKind:
            if kind not in (EffectResultKind.SUCCEEDED, EffectResultKind.FAILED,
                    EffectResultKind.UNSUPPORTED, EffectResultKind.UNCERTAIN):
                continue
            self.assertIsNone(m.configuration_invocation_completion_for_result(
                context, result(request, kind=kind, profile=False)))
        for bad in (None, {}, [], False, "untrusted-profile"):
            value = replace(result(request), evidence={KEY: bad})
            with self.subTest(bad=bad), self.assertRaises(RuntimeEffectContractError):
                m.configuration_invocation_completion_for_result(context, value)

    def test_profile_refuses_uncertain_unsupported_or_wrong_effect_request_selection(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)
        values = [result(request, kind=EffectResultKind.UNCERTAIN),
            result(request, kind=EffectResultKind.UNSUPPORTED),
            replace(result(request), effect_id="another-original-event")]
        for field in ("request_fingerprint", "selection_fingerprint"):
            values.append(replace(result(request), evidence={KEY: {**payload(request), field: "f" * 64}}))
        for value in values:
            with self.subTest(kind=value.kind), self.assertRaises(RuntimeEffectContractError):
                m.configuration_invocation_completion_for_result(context, value)

    def test_partial_or_altered_context_selection_cannot_match_original_completion(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)
        a, b = context.selection.instances
        selections = (ConfigurationInstanceSelection((a,)),
            ConfigurationInstanceSelection((replace(a, allocation_id="new-incarnation"), b)),
            ConfigurationInstanceSelection((replace(a, content_digest="e" * 64), b)))
        for selection in selections:
            with self.subTest(selection=selection), self.assertRaises(RuntimeEffectContractError):
                m.configuration_invocation_completion_for_result(
                    replace(context, selection=selection), result(request))

    def test_full_result_ceiling_is_8192_with_and_without_completion_profile(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)

        def sized(target, include_profile):
            for count in range(30):
                evidence = {f"padding-{i:02d}": "x" * 512 for i in range(count)}
                if include_profile:
                    evidence[KEY] = payload(request)
                evidence["tail"] = ""
                seed = RuntimeEffectResult.succeeded(request.effect_id,
                    evidence=evidence, observations=(endpoint(),))
                remaining = target - len(rfc8785.dumps(seed.descriptor()))
                if 0 <= remaining <= 512:
                    evidence["tail"] = "x" * remaining
                    value = replace(seed, evidence=evidence)
                    self.assertEqual(len(rfc8785.dumps(value.descriptor())), target)
                    return value
            self.fail("fixture could not represent requested full-result size")

        for include_profile in (True, False):
            maximum, overflow = sized(8192, include_profile), sized(8193, include_profile)
            self.assertEqual(len(runtime_effect_result_fingerprint(maximum)), 64)
            observed = m.configuration_invocation_completion_for_result(context, maximum)
            self.assertEqual(observed is not None, include_profile)
            with self.assertRaises(RuntimeEffectContractError):
                m.configuration_invocation_completion_for_result(context, overflow)

    def test_reader_revalidates_mutated_context_and_complete_result_before_absence(self):
        m = language()
        request = selected_request()
        context = m.configuration_invocation_correlation_for_request(request)
        forged_context = deepcopy(context)
        object.__setattr__(forged_context, "kind", RuntimeEffectKind.REALIZE_ACTIVITY)
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_invocation_completion_for_result(forged_context, result(request))
        for field, bad in (("evidence", {"ordinary": object()}), ("observations", [endpoint()]),
                ("failure", RuntimeEffectFailure("failure", "Failed.")), ("kind", "succeeded")):
            value = result(request, profile=False)
            object.__setattr__(value, field, bad)
            with self.subTest(field=field), self.assertRaises(RuntimeEffectContractError):
                m.configuration_invocation_completion_for_result(context, value)

    def test_selection_and_request_helpers_revalidate_mutated_nominal_values(self):
        m = language()
        request = selected_request()
        selection = deepcopy(request.configuration_instances)
        object.__setattr__(selection, "instances", (selection.instances[0],) * 2)
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_invocation_selection_fingerprint(selection)
        object.__setattr__(request, "configuration_instances", selection)
        with self.assertRaises(RuntimeEffectContractError):
            m.configuration_invocation_correlation_for_request(request)


if __name__ == "__main__":
    unittest.main()
