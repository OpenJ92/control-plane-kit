"""Approved command-result digest law over the historical four-way outcome sum."""
from copy import deepcopy
from dataclasses import replace
import itertools
import struct
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_control_canonical_wire as canonical
from tests import test_node_control_result_variants as historical
from tests.test_receiver_node_control import ReceiverNodeControlFixtures, trim_to_bound


class ReceiverNodeControlResultTests(ReceiverNodeControlFixtures, unittest.TestCase):
    module_name = "receiver_node_control_results"

    def result_context(self, *, variable=None, operation=core.NodeControlOperation.APPLY_COMMAND, **changes):
        variable = self.variable() if variable is None else variable
        declaration = self.declaration(variable)
        request = self.request(variable=variable, declaration=declaration, operation=operation, **changes)
        return request, declaration, self.api.ReceiverNodeControlResultCodec(request, declaration)

    def outcomes(self, request, variable=None, state=None):
        variable = self.variable() if variable is None else variable
        state = next(s for v, s in self.variables() if v.kind is variable.kind) if state is None else state
        code = core.NodeControlEvidenceCode
        if request.operation is core.NodeControlOperation.READ_STATE:
            success = [core.NodeControlReadStateSucceeded(request.request_id, variable.state_codec, 4, state)]
            rejections = (code.NOT_AUTHORIZED,)
        else:
            success = [core.NodeControlTransitionSucceeded(request.request_id, 5, core.NodeControlEvidence(c))
                       for c in (code.APPLIED, code.NO_CHANGE)]
            rejections = (code.PRECONDITION_FAILED, code.INVALID_COMMAND, code.NOT_AUTHORIZED)
        return success + [core.NodeControlRejected(request.request_id, request.operation, core.NodeControlEvidence(c))
                          for c in rejections] + [core.NodeControlFailed(request.request_id, request.operation)]

    def test_every_outcome_has_actual_origin_digest_and_exact_flat_wire(self):
        for variable, state in self.variables():
            for operation in core.NodeControlOperation:
                request, declaration, codec = self.result_context(variable=variable, operation=operation)
                for outcome in self.outcomes(request, variable, state):
                    result = codec.result(outcome)
                    expected = outcome.descriptor() | {"profile": "workload-node-control-result.v2",
                                                        "request_digest": request.canonical_digest().value}
                    self.assertEqual(result, self.api.ReceiverNodeControlResult(request, declaration, outcome))
                    self.assertIs(result.profile, self.api.ReceiverNodeControlResultProfile.V2)
                    self.assertEqual(result.request_id, request.request_id)
                    self.assertEqual(result.request_digest, request.canonical_digest())
                    self.assertIs(result.operation, outcome.operation)
                    self.assertIs(result.status, outcome.status)
                    self.assertIs(result.codec, outcome.codec)
                    self.assertEqual(result.outcome, outcome)
                    self.assertEqual(result.descriptor(), expected)
                    self.assertEqual(result.canonical_bytes(), rfc8785.dumps(expected))
                    self.assertEqual(len(result.canonical_bytes()) - len(rfc8785.dumps(outcome.descriptor())), 128)
                    self.assertNotIn("outcome", expected)
                    self.assertNotIn("canonicalization", expected)
                    self.assertNotIn("target-a", repr(result))
                    self.assertNotIn("request-1", repr(result))
                    self.check_codec(codec, result)

    def test_same_id_and_operation_cannot_rebind_any_outcome_to_changed_request(self):
        for operation in core.NodeControlOperation:
            request, declaration, codec = self.result_context(operation=operation)
            changes = [dict(target=self.changed_target(request.target, n)) for n in
                       ("workspace_id", "runtime_id", "node_id", "receiver_id")]
            changes += [dict(authority_context=replace(request.authority_context, **{n: "other"})) for n in
                        ("authored_graph_id", "realized_projection_id")]
            changes += [dict(idempotency_key="other")]
            if operation is core.NodeControlOperation.APPLY_COMMAND:
                changes += [dict(precondition=core.ControlPlaneTransitionPrecondition(5)),
                            dict(payload=core.NodeControlPayload(request.command_codec, core.ScalarControlState("other")))]
            contexts = [(replace(request, **change), declaration) for change in changes]
            different = self.declaration(replace(self.variable(), description="Different declaration."))
            contexts.append((replace(request, declaration_identity=different.identity()), different))
            socket = replace(request.target.provider_socket_name, value="other")
            other_declaration = replace(declaration, surface=replace(declaration.surface, provider_socket_name=socket))
            contexts.append((replace(request, target=replace(request.target, provider_socket_name=socket),
                                     declaration_identity=other_declaration.identity()), other_declaration))
            other_variable = replace(self.variable(), variable_name=replace(request.variable_name, value="other"))
            other_declaration = self.declaration(other_variable)
            contexts.append((replace(request, variable_name=other_variable.variable_name,
                                     declaration_identity=other_declaration.identity()), other_declaration))
            for candidate, expected_declaration in contexts:
                self.assertEqual(candidate.request_id, request.request_id)
                self.assertIs(candidate.operation, request.operation)
                self.assertNotEqual(candidate.canonical_digest(), request.canonical_digest())
                other = self.api.ReceiverNodeControlResultCodec(candidate, expected_declaration)
                for outcome in self.outcomes(request):
                    result = codec.result(outcome)
                    self.refusal(lambda: other.decode(result.descriptor()))
                    self.refusal(lambda: other.decode_canonical_bytes(result.canonical_bytes()))
                    self.refusal(lambda: other.encode(result))
                    fresh = other.result(outcome)
                    self.assertEqual(fresh.request_digest, candidate.canonical_digest())

    def test_missing_or_wrong_digest_is_never_backfilled_from_expected_request(self):
        for operation in core.NodeControlOperation:
            request, declaration, codec = self.result_context(operation=operation)
            for outcome in self.outcomes(request):
                wire = codec.result(outcome).descriptor()
                for field in ("request_digest", "profile"):
                    missing = {k: v for k, v in wire.items() if k != field}
                    self.refusal(lambda: codec.decode(missing))
                    self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(missing)))
                for change in ({"request_digest": "f"*64}, {"request_digest": None}, {"request_digest": "A"*64},
                               {"request_digest": "a"*63}, {"request_id": "other"}, {"profile": "workload-node-control-result.v1"},
                               {"profile": "workload-node-health-read-result.v2"}):
                    self.refusal(lambda: codec.decode(wire | change))
                self.refusal(lambda: codec.result(replace(outcome, request_id="other")))
                self.refusal(lambda: codec.result(object()))
                self.refusal(lambda: self.api.ReceiverNodeControlResult(object(), declaration, outcome))
                self.refusal(lambda: self.api.ReceiverNodeControlResultCodec(object(), declaration))

    def test_complete_status_evidence_matrix_and_old_variable_rules_survive(self):
        fixture = historical.NodeControlResultVariantTests()
        codes = core.NodeControlEvidenceCode
        valid = {
            (core.NodeControlOperation.APPLY_COMMAND, core.NodeControlResultStatus.SUCCEEDED, codes.APPLIED),
            (core.NodeControlOperation.APPLY_COMMAND, core.NodeControlResultStatus.SUCCEEDED, codes.NO_CHANGE),
            (core.NodeControlOperation.READ_STATE, core.NodeControlResultStatus.REJECTED, codes.NOT_AUTHORIZED),
            *((core.NodeControlOperation.APPLY_COMMAND, core.NodeControlResultStatus.REJECTED, c)
              for c in (codes.PRECONDITION_FAILED, codes.INVALID_COMMAND, codes.NOT_AUTHORIZED)),
            *((op, core.NodeControlResultStatus.FAILED, codes.INTERNAL_FAILURE) for op in core.NodeControlOperation),
        }
        for operation, status, evidence in itertools.product(core.NodeControlOperation, core.NodeControlResultStatus, codes):
            request, _, codec = self.result_context(operation=operation, request_id="request-matrix-1")
            wire = fixture.matrix_descriptor(operation, status, evidence) | {
                "profile": "workload-node-control-result.v2", "request_digest": request.canonical_digest().value}
            if (operation, status, evidence) in valid:
                self.assertEqual(codec.encode(codec.decode(wire)), wire)
            else:
                self.refusal(lambda: codec.decode(wire))
        request, _, codec = self.result_context(operation=core.NodeControlOperation.READ_STATE)
        wrong_state = core.NodeControlReadStateSucceeded(request.request_id, core.ControlPlaneStateCodec.MAP_V1, 4,
                                                       core.MapControlState((("a", True),)))
        self.refusal(lambda: codec.result(wrong_state))
        self.refusal(lambda: codec.result(core.NodeControlTransitionSucceeded(request.request_id, 5, core.NodeControlEvidence(codes.APPLIED))))
        for outcome in self.outcomes(request):
            result = codec.result(outcome)
            if outcome.status is not core.NodeControlResultStatus.SUCCEEDED:
                for extra in ({"state": core.ScalarControlState("stale").descriptor()}, {"version": 9},
                              {"state_codec": "control.scalar.v1"}, {"payload": {"value": "stale"}}):
                    self.refusal(lambda: codec.decode(result.descriptor() | extra))
        # A declaration's health capability neither removes nor changes its variable contract.
        mixed = core.WorkloadNodeControlSurfaceDeclaration(replace(self.declaration().surface,
            health_reads=(core.NodeHealthReadKind.READINESS,)), profile=core.WorkloadNodeControlSurfaceDeclarationProfile.V2)
        mixed_request = replace(request, declaration_identity=mixed.identity())
        mixed_codec = self.api.ReceiverNodeControlResultCodec(mixed_request, mixed)
        self.assertEqual(mixed_codec.decode(mixed_codec.result(self.outcomes(mixed_request)[0]).descriptor()).request, mixed_request)

    def test_forged_results_contexts_and_private_material_are_revalidated(self):
        request, declaration, codec = self.result_context()
        outcome = self.outcomes(request)[0]
        result = codec.result(outcome)
        for field, value in (("request", object()), ("declaration", object()), ("outcome", object())):
            forged = deepcopy(result)
            object.__setattr__(forged, field, value)
            self.refusal(lambda: codec.encode(forged))
            forged = deepcopy(result)
            object.__delattr__(forged, field)
            self.refusal(lambda: codec.encode(forged))
        forged_outcome = deepcopy(outcome)
        object.__setattr__(forged_outcome, "version", True)
        self.refusal(lambda: codec.result(forged_outcome))
        for field in ("authority_context", "target", "declaration_identity", "request_id", "payload"):
            given_request, _, other = self.result_context()
            object.__setattr__(given_request, field, object())
            self.refusal(lambda: other.encode(result))
            self.refusal(lambda: other.decode(result.descriptor()))
        for key in ("signature", "diagnostic", "endpoint", "token", "authority_context", "target"):
            self.refusal(lambda: codec.decode(result.descriptor() | {key: "private-test-value"}))
        different = self.declaration(replace(self.variable(), description="Different declaration."))
        self.refusal(lambda: self.api.ReceiverNodeControlResultCodec(request, different))

    def test_successor_result_cap_includes_profile_and_digest_overhead(self):
        variable = self.variables()[1][0]
        request, _, codec = self.result_context(variable=variable, operation=core.NodeControlOperation.READ_STATE)
        outcome = self.outcomes(request, variable)[0]
        document = codec.result(outcome).descriptor()
        document["state"]["entries"] = {f"v{i:03d}": "a"*128 for i in range(128)}
        exact, overflow = trim_to_bound(document, 16384, [("state", "entries", f"v{i:03d}") for i in range(128)])
        self.assertEqual(len(rfc8785.dumps(exact)), 16384)
        self.assertEqual(len(rfc8785.dumps(overflow)), 16385)
        result = codec.decode(exact)
        self.assertEqual(len(result.canonical_bytes()), core.MAX_NODE_CONTROL_PAYLOAD_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(result.canonical_bytes()), result)
        for wire in (exact, overflow):
            old_shape = {k: v for k, v in wire.items() if k not in {"profile", "request_digest"}}
            self.assertEqual(len(rfc8785.dumps(wire)) - len(rfc8785.dumps(old_shape)), 128)
            # Even the overflow still has a valid historical outcome: refusal is the new envelope cap.
            self.assertEqual(core.NodeControlResultCodec(variable).decode(old_shape).request_id, request.request_id)
        self.refusal(lambda: codec.decode(overflow))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))

    def test_result_raw_numbers_and_historical_wire_remain_disjoint(self):
        request, _, codec = self.result_context(operation=core.NodeControlOperation.READ_STATE)
        for vector in canonical.NodeControlCanonicalWireTests().fixture()["rfc8785_number_vectors"]:
            number = struct.unpack(">d", bytes.fromhex(vector["ieee754_hex"]))[0]
            outcome = self.outcomes(request, state=core.ScalarControlState(number))[0]
            result = codec.result(outcome)
            self.assertEqual(codec.decode_canonical_bytes(result.canonical_bytes()), result)
        outcome = self.outcomes(request, state=core.ScalarControlState(1e20))[0]
        raw = codec.result(outcome).canonical_bytes()
        for replacement in (b"100000000000000000001", b"1e20", b"-0", b"1e400"):
            self.refusal(lambda: codec.decode_canonical_bytes(raw.replace(b"100000000000000000000", replacement)))
        old_codec = core.NodeControlResultCodec(self.variable())
        self.refusal(lambda: codec.decode(old_codec.encode(outcome)))
        self.refusal(lambda: codec.encode(outcome))
        with self.assertRaises(ValueError):
            old_codec.decode(codec.result(outcome).descriptor())
        with self.assertRaises(ValueError):
            old_codec.encode(codec.result(outcome))

    def test_public_exports_are_exact(self):
        self.check_exports(self.api, ("ReceiverNodeControlResultProfile", "ReceiverNodeControlResult", "ReceiverNodeControlResultCodec"))


if __name__ == "__main__":
    unittest.main()
