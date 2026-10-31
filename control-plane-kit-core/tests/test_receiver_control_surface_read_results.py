"""V3 correlation and unchanged declaration-dependent coverage semantics."""
from copy import deepcopy
from dataclasses import replace
from typing import get_args
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_control_surface_read_results as historical
from tests import test_node_health_declarations as health
from tests.test_receiver_control_surface_reads import ReceiverControlSurfaceFixtures


class ReceiverControlSurfaceResultTests(ReceiverControlSurfaceFixtures, unittest.TestCase):
    module_name = "receiver_control_surface_read_results"

    def context_values(self, declaration=None, kind=core.NodeControlSurfaceReadKind.STATUS, **changes):
        declaration = self.declaration() if declaration is None else declaration
        target = replace(self.target(), provider_socket_name=declaration.surface.provider_socket_name)
        request = self.request(target=target, declaration_identity=declaration.identity(), kind=kind, **changes)
        return request, declaration, self.api.ReceiverControlSurfaceReadResultCodec(request, declaration)

    def installed(self, *names):
        return tuple(core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.VARIABLE, name) for name in names)

    def test_exact_v3_variants_and_declaration_appropriate_payloads(self):
        self.assertEqual(set(get_args(self.api.ReceiverControlSurfaceReadResult)), {
            self.api.ReceiverControlSurfaceCapabilitiesResult, self.api.ReceiverControlSurfaceStatusResult})
        for declaration in (self.declaration(), health.NodeHealthDeclarationTests().declaration("alpha", "beta"),
                            health.NodeHealthDeclarationTests().declaration()):
            for kind in core.NodeControlSurfaceReadKind:
                request, declaration, codec = self.context_values(declaration, kind)
                common = {"profile": "workload-node-control-surface-read-result.v3",
                          "canonicalization": "jcs-rfc8785.v1", "request_id": request.request_id,
                          "request_digest": request.canonical_digest().value, "kind": kind.value,
                          "declaration_identity": declaration.identity().value}
                if kind is core.NodeControlSurfaceReadKind.CAPABILITIES:
                    result = codec.capabilities_result()
                    expected = common | {"declaration": declaration.descriptor()}
                else:
                    result = codec.status_result(())
                    key = "registry_coverage" if declaration.profile is core.WorkloadNodeControlSurfaceDeclarationProfile.V1 else "variable_registry_coverage"
                    expected = common | {"installed_variable_names": [], key: "none"}
                    self.assertIs(result.registry_coverage, core.NodeControlSurfaceRegistryCoverage.NONE)
                self.assertIs(result.profile, self.api.ReceiverControlSurfaceReadResultProfile.V3)
                self.assertIs(result.canonicalization, core.NodeControlCanonicalization.JCS_RFC8785_V1)
                self.assertEqual(result.request_id, request.request_id)
                self.assertEqual(result.request_digest, request.canonical_digest())
                self.assertEqual(result.declaration_identity, declaration.identity())
                self.assertIs(result.kind, kind)
                self.assertEqual(result.descriptor(), expected)
                self.assertEqual(result.canonical_bytes(), rfc8785.dumps(expected))
                self.assertEqual(codec.decode(expected), result)
                self.check_closed_raw_codec(codec, result)
                self.assertNotIn("surface-read-1", repr(result))
                self.assertNotIn("alpha", repr(result))

    def test_subset_and_coverage_are_derived_canonical_and_non_authoritative(self):
        for declaration in (self.declaration(), health.NodeHealthDeclarationTests().declaration("alpha", "beta")):
            _, declaration, codec = self.context_values(declaration)
            key = "registry_coverage" if declaration.profile is core.WorkloadNodeControlSurfaceDeclarationProfile.V1 else "variable_registry_coverage"
            for names, coverage in (((), "none"), (("alpha",), "partial"), (("alpha", "beta"), "complete")):
                result = codec.status_result(self.installed(*names))
                self.assertEqual(result.registry_coverage.value, coverage)
                self.assertEqual(codec.decode(result.descriptor()), result)
                for lie in {"none", "partial", "complete", "unknown"} - {coverage}:
                    self.refusal(lambda: codec.decode(result.descriptor() | {key: lie}))
            for bad in (self.installed("beta", "alpha"), self.installed("alpha", "alpha"), self.installed("other"),
                        list(self.installed("alpha")), ("alpha",),
                        (core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.NODE, "alpha"),)):
                self.refusal(lambda: codec.status_result(bad))
            descriptor = codec.status_result(self.installed("alpha")).descriptor()
            for names in (["beta", "alpha"], ["alpha", "alpha"], ["other"], [None], "alpha"):
                self.refusal(lambda: codec.decode(descriptor | {"installed_variable_names": names}))
            wrong_key = "variable_registry_coverage" if key == "registry_coverage" else "registry_coverage"
            self.refusal(lambda: codec.decode({wrong_key if k == key else k: v for k, v in descriptor.items()}))
            self.assertNotIn("health", descriptor)
            self.assertNotIn("state", descriptor)

    def test_exact_request_and_declaration_substitution_refusals(self):
        for kind in core.NodeControlSurfaceReadKind:
            request, declaration, codec = self.context_values(kind=kind)
            result = codec.capabilities_result() if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else codec.status_result(())
            changes = [dict(request_id="other"), dict(authority_context=self.context("B")),
                       dict(target=replace(request.target, receiver_id="b"*32)),
                       dict(kind=core.NodeControlSurfaceReadKind.STATUS if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else core.NodeControlSurfaceReadKind.CAPABILITIES)]
            changes += [dict(target=replace(request.target, **{name: replace(getattr(request.target, name), value="other")}))
                        for name in ("workspace_id", "runtime_id", "node_id")]
            for change in changes:
                changed = self.api.ReceiverControlSurfaceReadResultCodec(replace(request, **change), declaration)
                self.refusal(lambda: changed.encode(result))
                self.refusal(lambda: changed.decode(result.descriptor()))
            for change in ({"profile": "workload-node-control-surface-read-result.v1"},
                           {"profile": "workload-node-control-surface-read-result.v2"},
                           {"profile": "workload-node-health-read-result.v2"}, {"canonicalization": "other"},
                           {"kind": "read-state"}, {"request_id": "other"}, {"request_digest": "f"*64},
                           {"declaration_identity": "f"*64}):
                self.refusal(lambda: codec.decode(result.descriptor() | change))
            changed_declaration = historical.NodeControlSurfaceReadResultTests().declaration("alpha", "zeta")
            self.refusal(lambda: self.api.ReceiverControlSurfaceReadResultCodec(request, changed_declaration))
            if kind is core.NodeControlSurfaceReadKind.CAPABILITIES:
                self.assertEqual(len(declaration.canonical_bytes()), len(changed_declaration.canonical_bytes()))
                self.refusal(lambda: codec.decode(result.descriptor() | {"declaration": changed_declaration.descriptor()}))
                self.refusal(lambda: codec.status_result(()))
            else:
                self.refusal(codec.capabilities_result)
            wrong_socket = replace(request, target=replace(request.target,
                provider_socket_name=replace(request.target.provider_socket_name, value="other")))
            self.refusal(lambda: self.api.ReceiverControlSurfaceReadResultCodec(wrong_socket, declaration))

    def test_nominal_reconstruction_and_private_runtime_fields_refuse(self):
        request, declaration, codec = self.context_values()
        result = codec.status_result(self.installed("alpha"))
        for key in ("state", "version", "evidence", "payload", "endpoint", "signature", "error", "target",
                    "registry", "health", "readiness", "authority_context"):
            self.refusal(lambda: codec.decode(result.descriptor() | {key: "private-test-value"}))
        for field, value in (("request", object()), ("declaration", object()), ("installed_variable_names", ["alpha"])):
            forged = deepcopy(result)
            object.__setattr__(forged, field, value)
            self.refusal(lambda: codec.encode(forged))
        forged_reference = deepcopy(self.installed("alpha")[0])
        object.__setattr__(forged_reference, "value", "Bearer private-test-value")
        self.refusal(lambda: codec.status_result((forged_reference,)))
        class Text(str):
            pass
        object.__setattr__(forged_reference, "value", Text("alpha"))
        self.refusal(lambda: codec.status_result((forged_reference,)))
        for field in ("request", "declaration", "installed_variable_names"):
            forged = deepcopy(result)
            object.__delattr__(forged, field)
            self.refusal(lambda: codec.encode(forged))
        for subject, field, value in (("request", "authority_context", object()),
                                      ("surface", "variables", [])):
            # Corrupt the actual independently supplied context after construction.
            given_request, given_declaration, other = self.context_values()
            target = given_request if subject == "request" else given_declaration.surface
            object.__setattr__(target, field, value)
            self.refusal(lambda: other.decode(result.descriptor()))
            self.refusal(lambda: other.encode(result))

    def test_reachable_global_and_context_limits_preserve_both_declarations(self):
        fixture = historical.NodeControlSurfaceReadResultTests()
        for kind in core.NodeControlSurfaceReadKind:
            legacy = fixture.maximum_capability_declaration() if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else fixture.maximum_status_declaration()
            variables = list(legacy.surface.variables)
            if kind is core.NodeControlSurfaceReadKind.CAPABILITIES:
                variables[0] = replace(variables[0], description=variables[0].description[:-40])
            modern = core.WorkloadNodeControlSurfaceDeclaration(replace(legacy.surface,
                variables=tuple(variables), health_reads=(core.NodeHealthReadKind.LIVENESS, core.NodeHealthReadKind.READINESS)),
                profile=core.WorkloadNodeControlSurfaceDeclarationProfile.V2)
            for declaration in (legacy, modern):
                _, _, codec = self.context_values(declaration, kind, request_id="r"*128)
                if kind is core.NodeControlSurfaceReadKind.CAPABILITIES:
                    result, maximum, key = codec.capabilities_result(), 16902, "declaration"
                else:
                    result = codec.status_result(tuple(v.variable_name for v in declaration.surface.variables))
                    maximum = 4811 if declaration is legacy else 4820
                    key = "installed_variable_names"
                self.assertEqual(len(result.canonical_bytes()), maximum)
                self.assertEqual(codec.decode_canonical_bytes(result.canonical_bytes()), result)
                overflow = fixture.padded_candidate(result.descriptor(), key, maximum)
                self.refusal(lambda: codec.decode(overflow))
                self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))
                if kind is core.NodeControlSurfaceReadKind.STATUS:
                    over_count = result.descriptor() | {key: [f"n{i:03d}" for i in range(129)]}
                    self.assertLess(len(rfc8785.dumps(over_count)), maximum)
                    self.refusal(lambda: codec.decode(over_count))
            _, _, small = self.context_values(kind=kind)
            result = small.capabilities_result() if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else small.status_result(self.installed("alpha", "beta"))
            overflow = result.descriptor() | {"request_id": result.request_id + "x"}
            self.assertEqual(len(rfc8785.dumps(overflow)), len(result.canonical_bytes()) + 1)
            self.refusal(lambda: small.decode(overflow))

    def test_historical_results_are_disjoint_and_public_exports_exact(self):
        fixture = historical.NodeControlSurfaceReadResultTests()
        for kind in core.NodeControlSurfaceReadKind:
            request, declaration, codec = self.context_values(kind=kind)
            old_request = fixture.request(declaration, kind)
            old_codec = core.NodeControlSurfaceReadResultCodec(old_request, declaration)
            new_result = codec.capabilities_result() if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else codec.status_result(())
            old_result = old_codec.capabilities_result() if kind is core.NodeControlSurfaceReadKind.CAPABILITIES else old_codec.status_result(())
            self.refusal(lambda: codec.encode(old_result))
            self.refusal(lambda: codec.decode(old_result.descriptor()))
            self.refusal(lambda: self.api.ReceiverControlSurfaceReadResultCodec(old_request, declaration))
            with self.assertRaises(ValueError):
                old_codec.encode(new_result)
            with self.assertRaises(ValueError):
                old_codec.decode(new_result.descriptor())
            with self.assertRaises(ValueError):
                core.NodeControlSurfaceReadResultCodec(request, declaration)
        self.check_exports(self.api, ("ReceiverControlSurfaceReadResultProfile", "ReceiverControlSurfaceCapabilitiesResult",
            "ReceiverControlSurfaceStatusResult", "ReceiverControlSurfaceReadResult", "ReceiverControlSurfaceReadResultCodec"))


if __name__ == "__main__":
    unittest.main()
