"""N8 complete-or-refused bounds, using real stored material and cursor values."""

from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import unittest

from psycopg import sql
from psycopg.types.json import Jsonb

from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.records import (
    GraphVersionRecord, RealizedGraphProjectionRecord, WorkspaceRecord, _realized_projection_digest,
)
from tests.draft_catalogue_fixture import NOW
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_authoring_context_fixture import ReceiverAuthoringContextFixture, body_bytes


class ReceiverAuthoringContextBoundsTests(ReceiverAuthoringContextFixture, ReceiverAdmissionFixture, unittest.TestCase):
    def numbered_graph(self, count, *, content_size=None):
        graphs = []
        for number in range(count):
            graph, artifact, _ = self.receiver_graph(node_id=f"node-{number:02d}", receiver=f"{number + 1:032x}")
            if content_size is not None:
                content = artifact.content
                self.assertLessEqual(len(content.encode("utf-8")), content_size)
                # Whitespace is part of the selected original JSON bytes. No
                # invented field or change to the closed V2 grammar is needed.
                artifact = replace(artifact, content=content + " " * (content_size - len(content.encode("utf-8"))))
                node = graph.node(f"node-{number:02d}")
                graph = replace(graph, nodes={node.node_id: replace(node,
                    configuration_artifacts=(node.configuration_artifacts[0], artifact))})
            graphs.append(graph)
        return self.mixed_graph(*graphs)

    def test_64_total_source_bindings_succeed_and_the_65th_is_whole_refusal(self):
        first = self.catalogue().execute(self.receiver_create(graph=self.numbered_graph(32)))
        self.catalogue().execute(self.receiver_select(first))
        query = {"draft_id": first.draft_id, "expected_head_revision": 1}
        factory, observed = self.measured_factory()
        context = self.context_read(pending_draft=query, factory=factory)
        self.assertEqual([len(context[name]["receivers"]) for name in ("current", "desired", "pending_draft")],
                         [0, 32, 32])
        self.assert_measured_snapshot(observed)
        self.assertEqual([item["binding"]["node_id"] for item in context["desired"]["receivers"]],
                         [f"node-{number:02d}" for number in range(32)])
        revised = self.catalogue().execute(self.receiver_revise(first, graph=self.numbered_graph(33)))
        factory, observed = self.measured_factory()
        before = self.admission_truth()
        self.assert_context_refused(409, factory=factory,
            pending_draft={"draft_id": first.draft_id, "expected_head_revision": revised.revision})
        self.assert_measured_snapshot(observed)
        self.assertEqual(self.admission_truth(), before)

    def test_exact_64k_selected_json_whitespace_is_returned_without_recanonicalization(self):
        graph = self.numbered_graph(1, content_size=65536)
        self.desired_service().execute(self.desired_command(graph=graph))
        factory, observed = self.measured_factory()
        context = self.context_read(factory=factory)
        raw = context["desired"]["receivers"][0]["configuration_artifact"]["content"]
        self.assertEqual(raw, graph.node("node-00").configuration_artifacts[1].content)
        self.assertEqual(len(raw.encode("utf-8")), 65536)
        ReceiverNodeControlConfigurationCodec().decode_bytes(raw.encode("utf-8"))
        self.assert_measured_snapshot(observed)

    def test_json_escapes_and_utf8_are_counted_as_the_closed_body_serializer_specifies(self):
        graph, artifact, _ = self.receiver_graph()
        # Equivalent closed JSON with an original backslash escape, newline,
        # tab and quote characters; decoding must not rewrite retained bytes.
        raw = artifact.content.replace("workload-node-control-configuration", r"\u0077orkload-node-control-configuration", 1) + "\n\t"
        ReceiverNodeControlConfigurationCodec().decode_bytes(raw.encode("utf-8"))
        artifact = replace(artifact, content=raw)
        node = graph.node("api")
        graph = replace(graph, nodes={"api": replace(node,
            configuration_artifacts=(node.configuration_artifacts[0], artifact))})
        self.desired_service().execute(self.desired_command(graph=graph))
        value = self.context_read()
        self.assertEqual(value["desired"]["receivers"][0]["configuration_artifact"]["content"], raw)
        encoded = body_bytes(value)
        self.assertIn(b"\\\\u0077", encoded)
        self.assertIn(b"\\n\\t", encoded)
        # Config target references are ASCII-closed. UTF-8 instead belongs to
        # an existing legal receiver-free workspace identity, at its 2 KiB cap.
        workspace = "\U0001f30d" * 512
        self.assertEqual(len(workspace.encode("utf-8")), 2048)
        with self.unit_of_work() as uow:
            uow.stores.workspaces.create(WorkspaceRecord(workspace, "Unicode workspace"))
            uow.stores.graphs.save(GraphVersionRecord.from_graph(graph_id="unicode-current",
                workspace_id=workspace, version=1, graph=DeploymentGraph("empty"), created_by="operator-a", created_at=NOW))
            uow.stores.workspaces.set_current_graph(workspace, "unicode-current")
            uow.commit()
        unicode_value = self.context_read(workspace=workspace)
        self.assertEqual(unicode_value["workspace_id"], workspace)
        self.assertIn(workspace.encode("utf-8"), body_bytes(unicode_value))
        self.assertGreater(len(json.dumps(unicode_value, ensure_ascii=True).encode()), len(body_bytes(unicode_value)))

    def test_original_graph_and_action_oversize_never_cross_into_python_as_full_cells(self):
        first = self.desired_service().execute(self.desired_command())
        self.desired_service().execute(self.desired_command(graph=self.receiver_graph(pretty=True)[0], key="later"))
        # Distinct selected material forces enrichment of the original source.
        self.context_read()
        for table, column, identity, value, cap in (
            ("cpk_graph_versions", "graph_descriptor", "graph_id", first.graph_version_id, 1048576),
            ("cpk_realized_graph_projections", "graph_descriptor", "projection_id", first.desired_realized_projection_id, 1048576),
            ("cpk_operation_actions", "payload", "action_id", first.action.action_id, 65536),
        ):
            select = sql.SQL("SELECT {} FROM {} WHERE {}=%s").format(
                sql.Identifier(column), sql.Identifier(table), sql.Identifier(identity))
            update = sql.SQL("UPDATE {} SET {}=%s WHERE {}=%s").format(
                sql.Identifier(table), sql.Identifier(column), sql.Identifier(identity))
            original = self.connection.execute(select, (value,)).fetchone()[0]
            with self.subTest(table=table):
                # Deliberate corruption, not a lawful exact-cap positive. The
                # closed action grammar cannot acquire arbitrary filler fields.
                self.connection.execute(update, (Jsonb({"submitted-marker": "x" * (cap + 1)}), value))
                factory, observed = self.measured_factory()
                try:
                    self.assert_context_refused(409, factory=factory)
                    self.assert_measured_snapshot(observed)
                    self.assertLessEqual(observed[0].largest_cell, cap)
                finally:
                    self.connection.execute(update, (Jsonb(original), value))

    def test_invalid_selected_configuration_or_slot_is_whole_refusal_even_with_valid_outer_digest(self):
        graph, artifact, _ = self.receiver_graph(pretty=True)
        self.desired_service().execute(self.desired_command())
        publication = self.publication(graph=graph)
        self.publisher().execute(publication)
        original = publication.projection
        valid = self.context_read()
        node = graph.node("api")
        document = json.loads(artifact.content)
        malformed_v2 = json.dumps({key: value for key, value in document.items() if key != "verifiers"})
        duplicate_v2 = artifact.content.replace("{", '{"profile":"workload-node-control-configuration.v2",', 1)
        oversized_v2 = artifact.content + " " * (65537 - len(artifact.content.encode("utf-8")))
        self.assertEqual(len(oversized_v2.encode("utf-8")), 65537)
        variants = []
        for name, content in (("missing-verifiers", malformed_v2), ("duplicate-profile", duplicate_v2),
                              ("configuration-cap-plus-one", oversized_v2)):
            selected = replace(artifact, content=content)
            changed_node = replace(node, configuration_artifacts=(node.configuration_artifacts[0], selected))
            variants.append((name, replace(graph, nodes={"api": changed_node}), selected.content_digest))
        # Leave a perfectly valid receiver artifact in the graph, but select
        # the unrelated application file. Scanning for any valid V2 artifact
        # instead of following the installed environment association must fail.
        wrong_slot = replace(node, public_environment=(
            replace(node.public_environment[0], value="/etc/test/application.txt"),))
        variants.append(("selected-slot-mismatch", replace(graph, nodes={"api": wrong_slot}), artifact.content_digest))
        materials = []
        for name, changed_graph, selected_digest in variants:
            # Core's generic artifact/graph and projection digest remain valid;
            # it is specifically the selected receiver representation that fails.
            material = RealizedGraphProjectionRecord.from_graph(
                projection_id=original.projection_id, workspace_id=original.workspace_id,
                source_authored_graph_id=original.source_authored_graph_id,
                projection_kind=original.projection_kind, projection_key=original.projection_key,
                graph=changed_graph, created_by=original.created_by, created_at=original.created_at)
            materials.append((name, material.graph_descriptor, material.projection_digest, selected_digest))
        # Syntactically broken JSON cannot enter ConfigurationArtifact. Inject
        # only this additional corrupt consumer cell below that owner, retaining
        # the outer hashes via the existing pure digest function, not a codec shim.
        broken = deepcopy(dict(original.graph_descriptor))
        selected = next(item for item in broken["nodes"]["api"]["configuration_artifacts"]
                        if item["artifact_id"] == "selected")
        selected["content"] = '{"profile":'
        selected["content_digest"] = hashlib.sha256(selected["content"].encode()).hexdigest()
        digest = _realized_projection_digest(workspace_id=original.workspace_id,
            source_authored_graph_id=original.source_authored_graph_id,
            projection_kind=original.projection_kind, projection_key=original.projection_key,
            graph_descriptor=broken)
        materials.append(("malformed-json", broken, digest, selected["content_digest"]))
        messages = []
        for name, descriptor, digest, selected_digest in materials:
            with self.subTest(corruption=name):
                self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s, "
                    "projection_digest=%s WHERE projection_id=%s", (Jsonb(descriptor), digest, original.projection_id))
                self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
                    "WHERE realized_projection_id=%s", (selected_digest, original.projection_id))
                try:
                    before = self.admission_truth()
                    factory, observed = self.measured_factory()
                    messages.append(self.assert_context_refused(409, factory=factory))
                    self.assert_measured_snapshot(observed)
                    self.assertEqual(self.admission_truth(), before)
                finally:
                    self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s, "
                        "projection_digest=%s WHERE projection_id=%s",
                        (Jsonb(dict(original.graph_descriptor)), original.projection_digest, original.projection_id))
                    self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
                        "WHERE realized_projection_id=%s", (artifact.content_digest, original.projection_id))
        self.assertEqual(len(set(messages)), 1)
        self.assertEqual(self.context_read(), valid)

    def test_oversized_ancillary_actor_is_guarded_and_arbitrary_metadata_is_not_loaded(self):
        first = self.desired_service().execute(self.desired_command())
        # These are intentionally corrupt consumer inputs in disposable storage.
        original = self.connection.execute("SELECT actor_id FROM cpk_operation_actions WHERE action_id=%s",
                                           (first.action.action_id,)).fetchone()[0]
        self.connection.execute("UPDATE cpk_operation_actions SET actor_id=%s WHERE action_id=%s",
            ("private-marker" + "x" * 2049, first.action.action_id))
        try:
            factory, observed = self.measured_factory()
            self.assert_context_refused(409, factory=factory)
            self.assert_measured_snapshot(observed)
            self.assertFalse(observed[0].private_marker_seen)
        finally:
            self.connection.execute("UPDATE cpk_operation_actions SET actor_id=%s WHERE action_id=%s",
                (original, first.action.action_id))
        self.connection.execute("UPDATE cpk_workspaces SET metadata=%s WHERE workspace_id='workspace-a'",
            (Jsonb({"private-marker": "x" * 1_100_000}),))
        self.connection.execute("UPDATE cpk_graph_versions SET metadata=%s WHERE graph_id=%s",
            (Jsonb({"private-marker": "x" * 1_100_000}), first.graph_version_id))
        factory, observed = self.measured_factory()
        context = self.context_read(factory=factory)
        self.assertNotIn("private-marker", body_bytes(context).decode())
        self.assert_measured_snapshot(observed)
        self.assertFalse(observed[0].private_marker_seen)

    def test_exact_context_body_cap_then_one_more_byte_refuses_without_partial_receivers(self):
        graph = self.numbered_graph(12, content_size=40000)
        first = self.catalogue().execute(self.receiver_create(graph=graph))
        self.catalogue().execute(self.receiver_select(first))
        second = self.catalogue().execute(self.receiver_revise(first, graph=graph))
        baseline = self.context_read(pending_draft={"draft_id": first.draft_id, "expected_head_revision": 2})
        remaining = 1048576 - len(body_bytes(baseline))
        self.assertGreater(remaining, 0)
        nodes = dict(graph.nodes)
        for name, node in nodes.items():
            artifact = node.configuration_artifacts[1]
            extra = min(remaining, 65536 - len(artifact.content.encode("utf-8")))
            nodes[name] = replace(node, configuration_artifacts=(node.configuration_artifacts[0],
                replace(artifact, content=artifact.content + " " * extra)))
            remaining -= extra
        self.assertEqual(remaining, 0, "closed selected artifacts must have enough capacity for this boundary")
        exact_graph = replace(graph, nodes=nodes)
        third = self.catalogue().execute(self.receiver_revise(second, graph=exact_graph, key="exact-body"))
        # Changed IDs/digests have fixed widths; head 2 -> 3 is still one digit.
        # Only pending bytes grow: the selected desired graph stays unchanged.
        exact = self.context_read(pending_draft={"draft_id": first.draft_id, "expected_head_revision": 3})
        self.assertEqual(len(body_bytes(exact)), 1048576)
        last = nodes["node-11"]
        artifact = last.configuration_artifacts[1]
        self.assertLess(len(artifact.content.encode("utf-8")), 65536)
        nodes["node-11"] = replace(last, configuration_artifacts=(last.configuration_artifacts[0],
            replace(artifact, content=artifact.content + " ")))
        fourth = self.catalogue().execute(self.receiver_revise(third, graph=replace(graph, nodes=nodes), key="over-body"))
        self.assertEqual(fourth.revision, 4)
        self.assert_context_refused(409,
            pending_draft={"draft_id": first.draft_id, "expected_head_revision": 4})

    def test_distinct_original_sources_share_one_aggregate_transport_budget(self):
        # Each original is a genuine admitted graph with a large, unrelated
        # application file; the returned projection must exclude that file.
        # Cumulative receiver membership keeps every original relevant, while
        # shrinking selected application material prevents a per-cell refusal.
        graphs = []
        for number in range(12):
            graph, _, _ = self.receiver_graph(node_id=f"node-{number:02d}", receiver=f"{number + 1:032x}")
            graphs.append(graph)
            composed = self.mixed_graph(*graphs)
            node = composed.node(f"node-{number:02d}")
            application = replace(node.configuration_artifacts[0], content="x" * 240000)
            # Three real application artifacts, each within Core's own bound.
            artifacts = (application, node.configuration_artifacts[1],
                replace(application, artifact_id="application-two", target_path="/etc/test/two.txt"),
                replace(application, artifact_id="application-three", target_path="/etc/test/three.txt"))
            composed = replace(composed, nodes={**composed.nodes, node.node_id: replace(node, configuration_artifacts=artifacts)})
            self.desired_service().execute(self.desired_command(graph=composed, key=f"origin-{number}"))
            if number == 1:
                factory, observed = self.measured_factory()
                self.context_read(factory=factory)
                self.assert_measured_snapshot(observed)
        # Even one authored copy of each required origin exceeds the budget;
        # all selected sources, probes and provenance share this same allowance.
        self.assertGreater(12 * 3 * 240000, 8_388_608)
        factory, observed = self.measured_factory()
        self.assert_context_refused(409, factory=factory)
        self.assert_measured_snapshot(observed)
