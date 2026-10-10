"""Isomorphic v2 route language; full self/X/Y examples do not grant authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util
import json
from pathlib import Path
import unittest

import control_plane_kit_core as core
from tests.test_node_health_declarations import NodeHealthDeclarationTests


FIXTURES = Path(__file__).parent / "fixtures"


class ReceiverManagementRoutesTests(unittest.TestCase):
    def setUp(self):
        name = "control_plane_kit_core.receiver_management_routes"
        self.assertIsNotNone(importlib.util.find_spec(name), "#1963 shared route language is missing")
        self.api = importlib.import_module(name)
        self.codec = self.api.ReceiverManagementRoutesCodec()

    def raw(self, name="a"):
        # Explicit frozen wire vectors; the file's final newline is not wire data.
        return (FIXTURES / f"receiver-management-routes-{name}.json").read_bytes().rstrip(b"\n")

    def document(self, name="a"):
        return json.loads(self.raw(name))

    def refusal(self, action):
        with self.assertRaises(self.api.ReceiverManagementRoutesError) as caught:
            action()
        self.assertEqual(str(caught.exception), "gateway health relay configuration is invalid")
        self.assertEqual(vars(caught.exception), {})
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_frozen_v2_bytes_full_self_x_and_self_x_y_roundtrip(self):
        for name, count in (("a", 2), ("b", 3)):
            raw = self.raw(name)
            document = self.document(name)
            value = self.codec.decode_bytes(raw)
            self.assertEqual(self.codec.encode_bytes(value), raw)
            self.assertEqual(self.codec.encode(value), document)
            self.assertEqual(value.descriptor(), document)
            self.assertEqual(len(value.targets), count)
            self.assertEqual(value.targets[0].target, value.gateway_target)
            self.assertEqual(value.targets[1].declaration.surface.health_reads,
                             (core.NodeHealthReadKind.LIVENESS, core.NodeHealthReadKind.READINESS))
            document["targets"].reverse()
            self.assertEqual(self.codec.decode(document), value)
            self.assertEqual(self.codec.decode_bytes(json.dumps(document, indent=2).encode()), value)
        a = self.codec.decode_bytes(self.raw("a"))
        b = self.codec.decode_bytes(self.raw("b"))
        self.assertEqual(a.gateway_target, b.gateway_target)
        self.assertEqual(a.targets, b.targets[:2])

    def test_empty_and_nonself_documents_remain_values_not_graph_authority(self):
        value = self.codec.decode(self.document())
        for targets in ((), (value.targets[1],)):
            candidate = replace(value, targets=targets)
            self.assertEqual(self.codec.decode_bytes(self.codec.encode_bytes(candidate)), candidate)
        # Syntax does not prove the origin is the graph-derived endpoint.
        other = replace(value.targets[1], origin="http://other-host:80")
        self.assertEqual(replace(value, targets=(other,)).targets, (other,))

    def test_exact_types_nested_revalidation_immutability_and_redacted_repr(self):
        value = self.codec.decode(self.document())
        self.assertNotIn("workload-x", repr(value))
        self.assertNotIn("http://", repr(value.targets[1]))
        with self.assertRaises(FrozenInstanceError):
            value.targets = ()
        for name in ("ReceiverManagementRouteBinding", "ReceiverManagementRoutes",
                     "ReceiverManagementRoutesCodec", "ReceiverManagementRoutesError"):
            self.assertIs(getattr(core, name), getattr(self.api, name))
        self.refusal(lambda: self.codec.encode(object()))
        self.refusal(lambda: replace(value, targets=list(value.targets)))
        self.refusal(lambda: replace(value, targets=(object(),)))
        bad = replace(value.targets[1])
        object.__setattr__(bad, "origin", "http://private-marker/path")
        self.refusal(lambda: replace(value, targets=(bad,)))
        forged = replace(value)
        object.__setattr__(forged, "targets", (bad,))
        self.refusal(lambda: self.codec.encode_bytes(forged))
        target = replace(value.targets[1].target)
        object.__setattr__(target, "receiver_id", "private-marker")
        self.refusal(lambda: replace(value.targets[1], target=target))
        declaration = replace(value.targets[1].declaration)
        object.__setattr__(declaration, "surface", object())
        self.refusal(lambda: replace(value.targets[1], declaration=declaration))

    def test_duplicate_alias_receiver_and_cross_scope_refusal(self):
        value = self.codec.decode(self.document())
        binding = value.targets[1]
        for duplicate in (binding, replace(binding, target_id="another")):
            self.refusal(lambda: replace(value, targets=(*value.targets, duplicate)))
        for field, role in (("workspace_id", core.NodeControlGraphReferenceRole.WORKSPACE),
                            ("runtime_id", core.NodeControlGraphReferenceRole.RUNTIME)):
            foreign = replace(binding.target, **{field: core.NodeControlGraphReference(role, "foreign")})
            candidate = replace(binding, target=foreign)
            self.refusal(lambda: replace(value, targets=(candidate,)))

    def test_closed_documents_profiles_and_nested_socket_agreement(self):
        original = self.document()
        for update in ({"profile": "cpk-gateway-health-relay-configuration.v1"},
                       {"profile": "unknown"}, {"private-marker": "private-marker"},
                       {"targets": ()}, {"gateway_target": {}}, {"targets": ["x"]}):
            self.refusal(lambda: self.codec.decode({**original, **update}))
        for field in original:
            self.refusal(lambda: self.codec.decode({k: v for k, v in original.items() if k != field}))
        for edit in ("unknown", "missing", "socket", "declaration", "receiver", "target-type"):
            document = deepcopy(original)
            entry = document["targets"][1]
            if edit == "unknown":
                entry["private-marker"] = True
            elif edit == "missing":
                del entry["origin"]
            elif edit == "socket":
                entry["target"]["provider_socket_name"] = "other"
            elif edit == "declaration":
                entry["declaration"]["profile"] = "workload-node-control-surface-declaration.v1"
                entry["declaration"]["surface"].pop("health_reads")
            elif edit == "receiver":
                entry["target"]["receiver_id"] = "A" * 32
            else:
                entry["target"] = []
            self.refusal(lambda: self.codec.decode(document))

    def test_origin_and_target_id_grammar_preserves_existing_protocol(self):
        binding = self.codec.decode(self.document()).targets[1]
        longest_host = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61))
        for origin in ("http://localhost:1", "http://service.internal:65535", "http://127.0.0.1:80",
                       "http://" + longest_host + ":8080"):
            self.assertEqual(replace(binding, origin=origin).origin, origin)
        for origin in (None, "https://host:80", "http://HOST:80", "http://host", "http://host:0",
                       "http://host:65536", "http://host:080", "http://user@host:80",
                       "http://host:80/", "http://host:80?q=x", "http://host:80#x",
                       "http://[::1]:80", "http://under_score:80", "http://host.:80",
                       "http://host:80\n", "http://" + longest_host + "e:80"):
            self.refusal(lambda: replace(binding, origin=origin))
        self.assertEqual(replace(binding, target_id="a" + "b" * 127).target_id, "a" + "b" * 127)
        for target_id in ("", "A", "1a", "a/secret", "a" * 129, None, True):
            self.refusal(lambda: replace(binding, target_id=target_id))

    def test_binding_count_and_raw_byte_limits_have_positive_boundary_controls(self):
        value = self.codec.decode(self.document())
        bindings = tuple(replace(value.targets[1], target_id=f"t-{index:03}",
            target=replace(value.targets[1].target, receiver_id=f"{index:032x}")) for index in range(128))
        largest = replace(value, targets=bindings)
        self.assertEqual(len(self.codec.decode_bytes(self.codec.encode_bytes(largest)).targets), 128)
        extra = replace(bindings[0], target_id="extra", target=replace(bindings[0].target, receiver_id="f" * 32))
        self.refusal(lambda: replace(value, targets=(*bindings, extra)))
        raw = self.codec.encode_bytes(replace(value, targets=()))
        padded = raw + b" " * (131072 - len(raw))
        self.assertEqual(self.codec.decode_bytes(padded), replace(value, targets=()))
        self.refusal(lambda: self.codec.decode_bytes(padded + b" "))
        large = deepcopy(self.document())
        large["gateway_target"].update(workspace_id="w" * 128, runtime_id="r" * 128)
        large["targets"] = []
        for index in range(128):
            entry = deepcopy(self.document()["targets"][1])
            entry["target_id"] = f"t{index:03}" + "a" * 124
            entry["origin"] = "http://" + ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61)) + ":8080"
            entry["target"].update(workspace_id="w" * 128, runtime_id="r" * 128,
                                   node_id="n" * 128, receiver_id=f"{index:032x}")
            large["targets"].append(entry)
        self.assertGreater(len(json.dumps(large, sort_keys=True, separators=(",", ":")).encode()), 131072)
        self.refusal(lambda: self.codec.decode(large))

    def test_json_duplicates_nonfinite_floats_depth_and_encoding_refuse(self):
        raw = self.raw()
        nested_duplicate = raw.replace(b'"target_id":"x"', b'"target_id":"x","target_id":"y"')
        for bad in (b"", raw.decode(), bytearray(raw), b"\xff", b"private-marker", b"{}", b"[]",
                    b'{"profile":"x","profile":"y"}', nested_duplicate,
                    b'{"profile":NaN}', b'{"profile":Infinity}', b'{"profile":1.5}',
                    b"[" * 10 + b"]" * 10, raw + b"{}"):
            self.refusal(lambda: self.codec.decode_bytes(bad))

    def test_mixed_declaration_preserves_public_producer_depth_refusal(self):
        declaration = NodeHealthDeclarationTests().declaration("mode")
        self.assertEqual(core.WorkloadNodeControlSurfaceDeclarationCodec().decode(
            declaration.descriptor()), declaration)
        value = self.codec.decode(self.document())
        mixed = replace(value.targets[1], declaration=declaration)
        routes = replace(value, targets=(mixed,))
        self.assertEqual(routes.targets[0].declaration, declaration)
        # The old constructor permits this value, but its public artifact
        # producer decodes serialized bytes and refuses depth-nine contracts.
        raw = json.dumps(routes.descriptor(), sort_keys=True, separators=(",", ":")).encode()
        for name, action in (("encode", lambda: self.codec.encode(routes)),
                             ("encode_bytes", lambda: self.codec.encode_bytes(routes)),
                             ("decode", lambda: self.codec.decode(routes.descriptor())),
                             ("decode_bytes", lambda: self.codec.decode_bytes(raw))):
            with self.subTest(boundary=name):
                self.refusal(action)


if __name__ == "__main__":
    unittest.main()
