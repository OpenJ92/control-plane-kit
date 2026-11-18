"""Receiver scope and graph context are separate, closed pure values."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util
import json
import unittest

import rfc8785
import control_plane_kit_core as core
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget,
)


class ReceiverIdentityTests(unittest.TestCase):
    def setUp(self):
        self.references = tuple(NodeControlGraphReference(role, value) for role, value in (
            (NodeControlGraphReferenceRole.WORKSPACE, "workspace-a"),
            (NodeControlGraphReferenceRole.RUNTIME, "runtime-a"),
            (NodeControlGraphReferenceRole.NODE, "api"),
            (NodeControlGraphReferenceRole.PROVIDER_SOCKET, "control")))
        module = "control_plane_kit_core.receiver_identity"
        self.assertIsNotNone(importlib.util.find_spec(module), "receiver identity contract is not implemented")
        self.api = importlib.import_module(module)
        self.target = self.api.NodeControlReceiverTarget(*self.references, "a" * 32)
        self.codec = self.api.NodeControlReceiverTargetCodec()

    def refusal(self, action):
        with self.assertRaises(self.api.ReceiverIdentityError) as caught:
            action()
        self.assertEqual(str(caught.exception), "receiver identity is invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(vars(caught.exception), {})

    def test_exact_scope_roundtrip_and_independent_graph_contexts(self):
        expected = {"workspace_id": "workspace-a", "runtime_id": "runtime-a", "node_id": "api",
                    "provider_socket_name": "control", "receiver_id": "a" * 32}
        self.assertEqual(self.target.descriptor(), expected)
        self.assertEqual(self.codec.encode(self.target), expected)
        self.assertEqual(self.codec.encode_bytes(self.target), rfc8785.dumps(expected))
        self.assertEqual(self.codec.decode_bytes(json.dumps(expected, indent=1).encode()), self.target)
        context_codec = self.api.NodeControlAuthorityContextCodec()
        contexts = [self.api.NodeControlAuthorityContext("graph-" + v, "projection-" + v) for v in "ABC"]
        self.assertEqual(len(set(contexts)), 3)
        self.assertEqual(len({context_codec.encode_bytes(v) for v in contexts}), 3)
        for context in contexts:
            self.assertEqual(context_codec.decode(context_codec.encode(context)), context)
            self.assertEqual(context_codec.decode_bytes(context_codec.encode_bytes(context)), context)
            self.assertEqual(self.codec.decode(self.codec.encode(self.target)), self.target)
        self.assertEqual(context_codec.encode(contexts[0]),
                         {"authored_graph_id": "graph-A", "realized_projection_id": "projection-A"})
        for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name"):
            self.assertNotEqual(self.target, replace(self.target, **{name: replace(getattr(self.target, name), value="other")}))
        self.assertNotEqual(self.target, replace(self.target, receiver_id="b" * 32))
        with self.assertRaises(FrozenInstanceError):
            self.target.receiver_id = "b" * 32
        with self.assertRaises(FrozenInstanceError):
            contexts[0].authored_graph_id = "other"
        self.assertNotIn("workspace-a", repr(self.target))
        self.assertNotIn("projection-A", repr(contexts[0]))

    def test_receiver_and_reference_values_refuse_malformed_or_forged_material(self):
        class Text(str):
            pass
        for receiver_id in ("", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 31 + "\n",
                            True, None, b"a" * 32, Text("a" * 32)):
            with self.subTest(receiver_id=type(receiver_id).__name__):
                self.refusal(lambda: replace(self.target, receiver_id=receiver_id))
        for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name"):
            role = getattr(self.target, name).role
            other_role = (NodeControlGraphReferenceRole.RUNTIME if role is not NodeControlGraphReferenceRole.RUNTIME
                          else NodeControlGraphReferenceRole.WORKSPACE)
            self.refusal(lambda: replace(self.target, **{name: NodeControlGraphReference(other_role, "other")}))
            for value in ("", "a" * 129, "Bearer private-value", "https://private.invalid", Text("scope")):
                forged = deepcopy(getattr(self.target, name))
                object.__setattr__(forged, "value", value)
                self.refusal(lambda: replace(self.target, **{name: forged}))
        forged_target = deepcopy(self.target)
        object.__setattr__(forged_target, "receiver_id", "secret=private")
        self.refusal(lambda: self.codec.encode(forged_target))
        self.refusal(lambda: self.api.receiver_node_control_audience(forged_target))

    def test_authority_context_has_no_implicit_graph_role_or_private_fields(self):
        class Text(str):
            pass
        context = self.api.NodeControlAuthorityContext("graph-A", "projection-A")
        codec = self.api.NodeControlAuthorityContextCodec()
        for name in ("authored_graph_id", "realized_projection_id"):
            for value in ("", "a" * 129, "secret=private", "https://private.invalid", True, None,
                          self.references[0], Text("graph-A")):
                with self.subTest(name=name, kind=type(value).__name__):
                    self.refusal(lambda: replace(context, **{name: value}))
        for extra in ("graph_revision", "receiver_id", "runtime_id", "verifiers"):
            self.refusal(lambda: codec.decode({**codec.encode(context), extra: "private"}))
        for name in ("authored_graph_id", "realized_projection_id"):
            value = codec.encode(context)
            del value[name]
            self.refusal(lambda: codec.decode(value))
        forged = deepcopy(context)
        object.__setattr__(forged, "authored_graph_id", "Bearer private")
        self.refusal(lambda: codec.encode(forged))

    def test_closed_decoders_reject_duplicates_nonfinite_and_wrong_shapes(self):
        context_codec = self.api.NodeControlAuthorityContextCodec()
        context = self.api.NodeControlAuthorityContext("graph-A", "projection-A")
        for codec, value in ((self.codec, self.target), (context_codec, context)):
            document = codec.encode(value)
            raw = codec.encode_bytes(value)
            field = next(iter(document))
            duplicate = ("{" + json.dumps(field) + ":" + json.dumps(document[field]) + ",").encode() + raw[1:]
            for bad in (None, [], {**document, "extra": "private"}, {field: document[field]}):
                self.refusal(lambda: codec.decode(bad))
            for bad in (b"", b"\xff", b"{", b"NaN", duplicate, bytearray(raw), raw.decode()):
                self.refusal(lambda: codec.decode_bytes(bad))
            self.refusal(lambda: codec.encode(object()))

    def test_exact_aggregate_bounds_are_reachable_and_first_overflow_refuses(self):
        maximum = self.api.NodeControlReceiverTarget(*(
            NodeControlGraphReference(ref.role, "a" * 128) for ref in self.references), "b" * 32)
        context = self.api.NodeControlAuthorityContext("a" * 128, "b" * 128)
        for codec, value, exported, expected in (
            (self.codec, maximum, self.api.MAX_NODE_CONTROL_RECEIVER_TARGET_BYTES, 635),
            (self.api.NodeControlAuthorityContextCodec(), context, self.api.MAX_NODE_CONTROL_AUTHORITY_CONTEXT_BYTES, 308),
        ):
            raw = codec.encode_bytes(value)
            self.assertEqual(exported, expected)
            self.assertEqual(len(raw), expected)
            self.assertEqual(raw, rfc8785.dumps(value.descriptor()))
            self.assertEqual(codec.decode_bytes(raw), value)
            self.refusal(lambda: codec.decode_bytes(raw + b" "))

    def test_audience_is_routing_text_not_complete_scope_or_legacy_admission(self):
        self.assertEqual(self.api.receiver_node_control_audience(self.target), "workload:api:control")
        other = replace(self.target, workspace_id=NodeControlGraphReference(NodeControlGraphReferenceRole.WORKSPACE, "other"))
        self.assertEqual(self.api.receiver_node_control_audience(other), "workload:api:control")
        self.assertNotEqual(self.target, other)
        old = NodeControlTarget(self.references[0],
                                NodeControlGraphReference(NodeControlGraphReferenceRole.GRAPH_REVISION, "graph-A"),
                                self.references[2], self.references[3])
        self.assertNotEqual(old, self.target)
        self.refusal(lambda: self.codec.decode(old.descriptor()))
        self.refusal(lambda: self.api.receiver_node_control_audience(old))

    def test_public_root_exports_are_the_selected_new_value_contract(self):
        for name in ("ReceiverIdentityError", "NodeControlReceiverTarget", "NodeControlAuthorityContext",
                     "NodeControlReceiverTargetCodec", "NodeControlAuthorityContextCodec",
                     "receiver_node_control_audience", "MAX_NODE_CONTROL_RECEIVER_TARGET_BYTES",
                     "MAX_NODE_CONTROL_AUTHORITY_CONTEXT_BYTES"):
            self.assertIs(getattr(core, name), getattr(self.api, name))
            self.assertIn(name, core.__all__)


if __name__ == "__main__":
    unittest.main()
