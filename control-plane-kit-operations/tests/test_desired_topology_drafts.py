from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import unittest
from unittest.mock import patch

import psycopg

from control_plane_kit_core.algebra import RequirementSocket
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.cpk_server import CpkServerApplicationError
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey, OperationCommandService

from draft_catalogue_fixture import DraftCatalogueFixture, NOW, principal


class DesiredTopologyDraftTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.require_catalogue_interface()
        self.require_catalogue_relations()

    def test_two_drafts_and_prior_revision_remain_independent_without_runtime_writes(self):
        before = self.runtime_truth()
        first = self.create(title="submitted-title-first", graph=self.graph("first"))
        second = self.create(key="second", title="submitted-title-second", graph=self.graph("second"))
        revised = self.catalogue().execute(self.revise_command(first))

        self.assertNotEqual(first.draft_id, second.draft_id)
        self.assertEqual((first.revision, second.revision, revised.revision), (1, 1, 2))
        self.assertEqual(revised.draft_id, first.draft_id)
        self.assertNotEqual(first.graph_id, revised.graph_id)
        with self.unit_of_work() as uow:
            for result, name in ((first, "first"), (second, "second"), (revised, "revised")):
                graph = uow.stores.graphs.get(result.graph_id)
                self.assertEqual(graph.workspace_id, "workspace-a")
                self.assertEqual(graph.graph_descriptor["name"], name)
        summaries = self.read()["items"]
        self.assertEqual({item["draft_id"]: item["head_revision"] for item in summaries},
                         {first.draft_id: 2, second.draft_id: 1})
        history = self.read("read.desired-topology-draft-revisions", draft_id=first.draft_id)
        self.assertEqual([(item["revision"], item["graph_id"]) for item in history["items"]],
                         [(1, first.graph_id), (2, revised.graph_id)])
        detail = self.read("read.desired-topology-draft-revision", draft_id=first.draft_id, revision=1)
        self.assertEqual((detail["draft_id"], detail["revision"], detail["graph_id"]),
                         (first.draft_id, 1, first.graph_id))
        self.assertEqual(detail["graph_descriptor"]["name"], "first")
        actions = [row[0] for row in self.rows("cpk_operation_actions")
                   if row[0]["action_type"] in {"create-desired-topology-draft", "revise-desired-topology-draft"}]
        self.assertEqual(len(actions), 3)
        self.assertEqual({(a["payload"]["draft_id"], a["payload"]["revision"], a["payload"]["graph_id"])
                          for a in actions},
                         {(r.draft_id, r.revision, r.graph_id) for r in (first, second, revised)})
        for action in actions:
            self.assertEqual(set(action["payload"]), {"workspace_id", "draft_id", "revision", "graph_id"})
            self.assertEqual(action["payload"]["workspace_id"], "workspace-a")
            for key in ("workspace_id", "draft_id", "graph_id"):
                self.assertIsInstance(action["payload"][key], str)
                self.assertTrue(1 <= len(action["payload"][key]) <= 512)
        self.assertNotIn("submitted-title-", json.dumps(actions))
        self.assertEqual(self.runtime_truth(), before)

    def test_title_accepts_bounds_and_rejects_empty_control_overbound_and_nontext_without_writes(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError

        accepted = [(self.create(key=f"valid-title-{len(title)}", title=title), title)
                    for title in ("A", "x" * 512)]
        summaries = {row["draft_id"]: row["title"] for row in self.read()["items"]}
        for result, title in accepted:
            self.assertEqual(summaries[result.draft_id], title)
        before = self.catalogue_truth()
        for index, title in enumerate(("", " ", "bad\ntitle", "x" * 513, None, 42)):
            with self.subTest(title=title):
                with self.assertRaises(DesiredTopologyDraftError):
                    self.catalogue().execute(self.create_command(key=f"invalid-title-{index}", title=title))
                self.assertEqual(self.catalogue_truth(), before)

    def test_first_publication_requires_semantically_valid_workspace_active_product(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError

        graph = self.graph()
        node = graph.nodes["app"]
        invalid = replace(graph, nodes={"app": replace(node, sockets=replace(node.sockets,
            requirements=(RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",), True),),
        ))})
        DEFAULT_GRAPH_CODEC.encode(invalid)  # Encodable is not equivalent to semantically valid.
        self.assertFalse(validate_graph(invalid).valid)
        first = self.create(key="valid")
        before = self.catalogue_truth()
        for command in (
            self.create_command(graph=invalid),
            self.create_command(workspace="workspace-b"),
            self.revise_command(first, graph=invalid),
        ):
            with self.subTest(workspace=command.context.workspace_id):
                with self.assertRaises(DesiredTopologyDraftError):
                    self.catalogue().execute(command)
                self.assertEqual(self.catalogue_truth(), before)
        with self.unit_of_work() as uow:
            registered = self.register_product(uow, "workspace-b")
            uow.stores.registered_products.revoke("workspace-a", registered.reference)
            uow.commit()
        before = self.catalogue_truth()
        for command in (self.create_command(key="revoked"), self.revise_command(first)):
            with self.assertRaises(DesiredTopologyDraftError):
                self.catalogue().execute(command)
            self.assertEqual(self.catalogue_truth(), before)

    def test_replay_precedes_allocation_and_later_session_product_and_head_admission(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftConflict

        create = self.create_command()
        first = self.catalogue().execute(create)
        revise = self.revise_command(first)
        second = self.catalogue().execute(revise)
        self.catalogue().execute(self.revise_command(first, key="third", expected=2))
        with self.unit_of_work() as uow:
            registered = self.register_product(uow, "workspace-b")
            uow.stores.registered_products.revoke("workspace-a", registered.reference)
            uow.commit()
        OperationCommandService(self.unit_of_work, clock=lambda: NOW, id_factory=lambda: "close-action").execute(
            CloseOperationSession(self.sessions["workspace-a"], "operator-a", IdempotencyKey("close"))
        )
        before = self.catalogue_truth()

        def allocation_forbidden():
            self.fail("replay/conflict must resolve before allocating a durable identity")

        service = self.catalogue(id_factory=allocation_forbidden)
        for command, original in ((create, first), (revise, second)):
            replay = service.execute(command)
            self.assertEqual((replay.draft_id, replay.revision, replay.graph_id),
                             (original.draft_id, original.revision, original.graph_id))
        for changed in (replace(create, title="Changed"), replace(revise, graph=self.graph("changed"))):
            with self.assertRaises(DesiredTopologyDraftConflict):
                service.execute(changed)
        self.assertEqual(self.catalogue_truth(), before)

    def test_replay_rejects_malformed_or_uncorrelated_action_coordinates_before_allocation(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError
        from psycopg.types.json import Jsonb

        create = self.create_command()
        first = self.catalogue().execute(create)
        revise = self.revise_command(first)
        second = self.catalogue().execute(revise)

        def allocation_forbidden():
            self.fail("invalid replay allocated an identity")

        for command, result in ((create, first), (revise, second)):
            original = result.descriptor()
            invalid_payloads = (
                {key: value for key, value in original.items() if key != "graph_id"},
                {**original, "extra": "private-marker"},
                {**original, "revision": True},
                {**original, "revision": "1"},
                {**original, "draft_id": "x" * 513},
                {**original, "draft_id": "missing-draft"},
                {**original, "graph_id": "workspace-a-current"},
                {**original, "revision": 99},
                first.descriptor() if command is revise else second.descriptor(),
            )
            for payload in invalid_payloads:
                with self.subTest(command=type(command).__name__, payload=payload):
                    self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                        "WHERE session_id=%s AND idempotency_key=%s",
                        (Jsonb(payload), command.session_id, command.idempotency_key.value))
                    before = self.catalogue_truth()
                    with self.assertRaises(DesiredTopologyDraftError) as error:
                        self.catalogue(id_factory=allocation_forbidden).execute(command)
                    self.assertLessEqual(len(str(error.exception)), 512)
                    self.assertNotIn("private-marker", str(error.exception))
                    self.assertEqual(self.catalogue_truth(), before)
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                "WHERE session_id=%s AND idempotency_key=%s",
                (Jsonb(original), command.session_id, command.idempotency_key.value))
            self.assertEqual(self.catalogue(id_factory=allocation_forbidden).execute(command), result)

    def test_concurrent_distinct_revision_commands_have_one_winner_and_reject_stale_head(self):

        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftConflict

        first = self.create()
        graph_count = len(self.rows("cpk_graph_versions"))
        commands = tuple(self.revise_command(
            first, key=f"race-{i}", graph=self.graph(f"candidate-{i}"),
            session_id=self.start_session("workspace-a"),
        ) for i in range(2))
        barrier = threading.Barrier(2)

        def compete(command):
            barrier.wait(timeout=10)
            try:
                return self.catalogue().execute(command)
            except DesiredTopologyDraftConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(compete, commands))
        winners = [value for value in outcomes if not isinstance(value, DesiredTopologyDraftConflict)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].revision, 2)
        self.assertEqual(len(self.rows("cpk_graph_versions")), graph_count + 1)
        history = self.read("read.desired-topology-draft-revisions", draft_id=first.draft_id)
        self.assertEqual([row["revision"] for row in history["items"]], [1, 2])
        before = self.catalogue_truth()
        with self.assertRaises(DesiredTopologyDraftConflict):
            self.catalogue().execute(self.revise_command(first, key="stale"))
        self.assertEqual(self.catalogue_truth(), before)

    def test_concurrent_identical_create_replays_one_durable_revision(self):
        command = self.create_command()
        barrier = threading.Barrier(2)

        def create(_):
            barrier.wait(timeout=10)
            return self.catalogue().execute(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual((results[0].draft_id, results[0].graph_id),
                         (results[1].draft_id, results[1].graph_id))
        self.assertEqual(len(self.rows("cpk_desired_topology_drafts")), 1)
        self.assertEqual(len(self.rows("cpk_desired_topology_draft_revisions")), 1)

    def test_action_failure_rolls_back_graph_revision_head_and_action_for_create_and_revise(self):
        first = self.create()
        for command, revision in ((self.create_command(key="rolled-back-create"), 1), (self.revise_command(first), 2)):
            before = self.catalogue_truth()
            with patch.object(PostgresActivityHistoryStore, "add_action", side_effect=RuntimeError("injected action failure")):
                with self.assertRaisesRegex(RuntimeError, "injected action failure"):
                    self.catalogue().execute(command)
            self.assertEqual(self.catalogue_truth(), before)
            result = self.catalogue().execute(command)
            self.assertEqual(result.revision, revision)

    def test_command_authority_and_session_workspace_are_checked_before_allocation(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError

        command = self.create_command()
        before = self.catalogue_truth()

        def allocation_forbidden():
            self.fail("unauthorized catalogue command allocated a durable identity")

        service = self.catalogue(id_factory=allocation_forbidden)
        for denied in (
            replace(command, context=principal(scopes=()).command_context("workspace-a")),
            replace(command, session_id=self.sessions["workspace-b"]),
        ):
            with self.assertRaises(DesiredTopologyDraftError):
                service.execute(denied)
        self.assertEqual(self.catalogue_truth(), before)

    def test_graph_bounds_and_redaction_do_not_return_submitted_sensitive_metadata(self):
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError

        graph = self.graph()
        node = graph.nodes["app"]
        graph = replace(graph, nodes={"app": replace(node, metadata={**node.metadata, "api_token": "private-marker"})})
        # Existing graph-read policy redacts operator metadata, even on retained intent.
        created = self.create(graph=graph)
        detail = self.read("read.desired-topology-draft-revision", draft_id=created.draft_id, revision=1)
        self.assertNotIn("private-marker", json.dumps(detail))
        self.assertNotIn("private-marker", json.dumps(created.descriptor()))
        self.assertNotIn("private-marker", json.dumps(self.rows("cpk_operation_actions")))
        oversized = replace(graph, nodes={"app": replace(node, metadata={**node.metadata, "description": "x" * 1_048_577})})
        before = self.catalogue_truth()
        with self.assertRaises(DesiredTopologyDraftError) as error:
            self.catalogue().execute(self.create_command(key="oversized", graph=oversized))
        self.assertLessEqual(len(str(error.exception)), 512)
        self.assertNotIn("x" * 1024, str(error.exception))
        self.assertEqual(self.catalogue_truth(), before)


class DesiredTopologyDraftSchemaTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.require_catalogue_relations()
        self.require_catalogue_interface()

    def test_fresh_catalogue_relations_exist_and_exact_reentry_preserves_saved_only_graph(self):
        first = self.create()
        before = self.catalogue_truth()
        identities = self.connection.execute(
            "SELECT oid, relfilenode FROM pg_class WHERE relnamespace = current_schema()::regnamespace ORDER BY oid"
        ).fetchall()
        install_schema(self.connection)
        self.assertEqual(self.catalogue_truth(), before)
        self.assertEqual(self.connection.execute(
            "SELECT oid, relfilenode FROM pg_class WHERE relnamespace = current_schema()::regnamespace ORDER BY oid"
        ).fetchall(), identities)
        self.assertFalse(any(row[0]["desired_graph_id"] == first.graph_id for row in self.rows("cpk_workspaces")))

    def test_database_rejects_cross_workspace_graph_and_duplicate_revision_identity(self):
        first = self.create()
        self.catalogue().execute(self.revise_command(first))
        before = self.catalogue_truth()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.connection.execute(
                "UPDATE cpk_desired_topology_draft_revisions SET graph_id = %s "
                "WHERE workspace_id = %s AND draft_id = %s AND revision = 1",
                ("workspace-b-current", "workspace-a", first.draft_id),
            )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.connection.execute(
                "UPDATE cpk_desired_topology_draft_revisions SET revision = 1 "
                "WHERE workspace_id = %s AND draft_id = %s AND revision = 2",
                ("workspace-a", first.draft_id),
            )
        self.assertEqual(self.catalogue_truth(), before)

    def test_schema_drift_requires_reset_without_repair_or_data_loss(self):
        self.create()
        self.connection.execute("ALTER TABLE cpk_desired_topology_drafts ADD COLUMN foreign_drift text")
        before = self.catalogue_truth()
        with self.assertRaisesRegex(SchemaInstallationError, "reset is required"):
            install_schema(self.connection)
        self.assertEqual(self.catalogue_truth(), before)

    def test_saved_only_invalid_graph_is_included_in_current_row_validation(self):
        first = self.create()
        self.connection.execute(
            "UPDATE cpk_graph_versions SET graph_descriptor = jsonb_set(graph_descriptor, '{name}', '\"\"'::jsonb) "
            "WHERE graph_id = %s", (first.graph_id,),
        )
        before = self.catalogue_truth()
        with self.assertRaisesRegex(SchemaInstallationError, "reset is required"):
            install_schema(self.connection)
        self.assertEqual(self.catalogue_truth(), before)


class DesiredTopologyDraftReadTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.require_catalogue_interface()
        self.require_catalogue_relations()

    def test_independent_pages_reject_foreign_collection_workspace_and_draft_cursors(self):
        first = self.create()
        second = self.create(key="second")
        earlier = self.catalogue(clock=lambda: "2026-09-06T17:59:59Z").execute(self.create_command(key="earlier"))
        self.catalogue().execute(self.revise_command(first))
        page = self.read(limit=1)
        following = self.read(limit=1, after=page["next_cursor"])
        final = self.read(limit=1, after=following["next_cursor"])
        # Ascending creation instant, then draft ID: creation order is deliberately different.
        self.assertEqual([row["draft_id"] for row in page["items"] + following["items"] + final["items"]],
                         [earlier.draft_id, *sorted((first.draft_id, second.draft_id))])
        self.assertIsNone(final["next_cursor"])
        revisions = self.read("read.desired-topology-draft-revisions", draft_id=first.draft_id, limit=1)
        next_revision = self.read("read.desired-topology-draft-revisions", draft_id=first.draft_id,
                                  limit=1, after=revisions["next_cursor"])
        self.assertEqual([r["revision"] for r in revisions["items"] + next_revision["items"]], [1, 2])
        self.assertIsNone(next_revision["next_cursor"])
        for route, values in (
            ("read.desired-topology-drafts", {"after": revisions["next_cursor"]}),
            ("read.desired-topology-drafts", {"workspace": "workspace-b", "after": page["next_cursor"]}),
            ("read.desired-topology-draft-revisions", {"draft_id": second.draft_id, "after": revisions["next_cursor"]}),
            ("read.desired-topology-drafts", {"after": {"format_version": 1, "collection": "run-events",
                "scope": {"workspace_id": "workspace-a", "run_id": "run-a"},
                "position": {"ordinal": 1, "event_id": "event-a"}}}),
        ):
            with self.subTest(route=route, values=values):
                with self.assertRaises(CpkServerApplicationError) as error:
                    self.read(route, **values)
                self.assertEqual(error.exception.status, 400)
        for limit in (0, 101):
            with self.assertRaises(CpkServerApplicationError) as error:
                self.read(limit=limit)
            self.assertEqual(error.exception.status, 400)

    def test_revision_detail_cannot_resolve_another_workspaces_draft(self):
        first = self.create()
        with self.assertRaises(CpkServerApplicationError) as error:
            self.read("read.desired-topology-draft-revision", workspace="workspace-b",
                      draft_id=first.draft_id, revision=1)
        self.assertEqual(error.exception.status, 404)
