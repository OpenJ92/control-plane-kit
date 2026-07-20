from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import os
import unittest
from urllib.parse import urlsplit

import psycopg

from control_plane_kit import (
    EndpointMaterial,
    EndpointScope,
    HttpCheck,
    LiteralEndpointMaterial,
    Protocol,
    VerificationCapability,
    VerificationCheckMaterial,
    VerificationCompleted,
    VerificationIdentity,
    VerificationInterpreterRegistry,
    VerificationOutcome,
)
from control_plane_kit.mcp_read import (
    ReadOnlyMcpAdapter,
)
from control_plane_kit.servers import (
    create_instance_read_app,
)
from control_plane_kit.cli import run as run_cli
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
    install_schema,
)
from control_plane_kit.workflows import (
    ExecuteVerification,
    VerificationAuthority,
    VerificationCommandConflict,
    VerificationCommandDenied,
    VerificationCommandService,
    VerificationScope,
)
from tests.postgres_case import PostgresStoreTestCase
from fastapi.testclient import TestClient


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


@dataclass
class TransactionTracker:
    active: bool = False


class TrackedUnitOfWork:
    def __init__(self, delegate: PostgresUnitOfWork, tracker: TransactionTracker) -> None:
        self._delegate = delegate
        self._tracker = tracker

    @property
    def stores(self):
        return self._delegate.stores

    def __enter__(self):
        self._delegate.__enter__()
        self._tracker.active = True
        return self

    def commit(self) -> None:
        self._delegate.commit()

    def __exit__(self, *args) -> None:
        try:
            self._delegate.__exit__(*args)
        finally:
            self._tracker.active = False


@dataclass(frozen=True)
class TransactionAssertingHttpInterpreter:
    tracker: TransactionTracker
    calls: list[VerificationCheckMaterial]

    @property
    def capabilities(self):
        return frozenset((VerificationCapability.HTTP,))

    def execute(self, material):
        if self.tracker.active:
            raise AssertionError("verification adapter executed inside UnitOfWork")
        self.calls.append(material)
        return VerificationCompleted(
            VerificationIdentity(
                material.node_id,
                material.graph_id,
                material.check.check_id,
            ),
            VerificationCapability.HTTP,
            VerificationOutcome.PASSED,
            1,
        )


@dataclass(frozen=True)
class RaisingHttpInterpreter:
    @property
    def capabilities(self):
        return frozenset((VerificationCapability.HTTP,))

    def execute(self, material):
        raise OSError("simulated transport loss")


class VerificationCommandServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(
            WorkspaceRecord(
                workspace_id="workspace-a",
                name="Verification",
                current_graph_id="graph-a",
                desired_graph_id="graph-a",
            )
        )
        self.stores.graph_topology.save(_graph("graph-a", "workspace-a", 1))
        self.tracker = TransactionTracker()
        self.calls: list[VerificationCheckMaterial] = []
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        self.factory = lambda: TrackedUnitOfWork(
            PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            self.tracker,
        )
        interpreter = TransactionAssertingHttpInterpreter(self.tracker, self.calls)
        self.service = VerificationCommandService(
            self.factory,
            VerificationInterpreterRegistry({VerificationCapability.HTTP: interpreter}),
            clock=lambda: NOW,
            id_factory=_ids("verification-intent-a", "verification-observation-a"),
        )

    def test_executes_outside_transaction_and_persists_canonical_observation(self) -> None:
        before = self.stores.workspace.get("workspace-a")

        result = self.service.execute(_command())

        after = self.stores.workspace.get("workspace-a")
        self.assertEqual((after.current_graph_id, after.desired_graph_id),
                         (before.current_graph_id, before.desired_graph_id))
        self.assertEqual(self.calls, [_material()])
        self.assertEqual(result.observation.status.value, "verified")
        self.assertEqual(result.intent.status.value, "starting")
        self.assertEqual(result.intent.probe_outcome.value, "unknown")
        self.assertEqual(result.observation.probe_kind.value, "semantic-verification")
        self.assertEqual(result.observation.probe_outcome.value, "verified")
        self.assertEqual(result.observation.subject_id, "verification:api:semantic-http")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            observed_state_store=self.stores.observed_state,
            clock=lambda: NOW,
        ).observed_state("workspace-a").descriptor()["observations"][0]
        self.assertEqual(descriptor["status"], "verified")
        self.assertEqual(descriptor["probe_kind"], "semantic-verification")
        self.assertEqual(descriptor["payload"]["identity"]["check_id"], "semantic-http")
        self.assertNotIn("http://api:8080", str(descriptor))

    def test_graph_change_marks_verification_stale_without_rewriting_it(self) -> None:
        self.service.execute(_command())
        self.stores.graph_topology.save(_graph("graph-b", "workspace-a", 2))
        self.stores.workspace.set_current_graph("workspace-a", "graph-b")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            observed_state_store=self.stores.observed_state,
            clock=lambda: NOW,
        ).observed_state("workspace-a").descriptor()["observations"][0]

        self.assertTrue(descriptor["stale"])
        self.assertEqual(descriptor["stale_reason"], "graph-changed")
        stored = self.stores.observed_state.latest(
            "workspace-a", "verification:api:semantic-http"
        )
        self.assertEqual(stored.graph_id, "graph-a")
        self.assertEqual(stored.freshness.value, "fresh")

    def test_authorization_and_graph_ownership_fail_before_effect(self) -> None:
        with self.assertRaises(VerificationCommandDenied):
            self.service.execute(
                ExecuteVerification(
                    "workspace-a",
                    _material(),
                    VerificationAuthority("operator", frozenset()),
                )
            )

        self.stores.workspace.create(WorkspaceRecord("workspace-b", "Other"))
        foreign = VerificationCheckMaterial(
            "api", "graph-a", _material().check, _material().endpoint
        )
        with self.assertRaises(VerificationCommandConflict):
            self.service.execute(
                ExecuteVerification(
                    "workspace-b",
                    foreign,
                    VerificationAuthority(
                        "operator", frozenset((VerificationScope.EXECUTE,))
                    ),
                )
            )
        self.assertEqual(self.calls, [])

    def test_unsupported_capability_is_durable_and_distinct_from_failure(self) -> None:
        service = VerificationCommandService(
            self.factory,
            VerificationInterpreterRegistry({}),
            clock=lambda: NOW,
            id_factory=_ids(
                "verification-unsupported-intent-a",
                "verification-unsupported-a",
            ),
        )

        result = service.execute(_command())

        self.assertEqual(result.observation.status.value, "unsupported")
        self.assertEqual(result.observation.probe_outcome.value, "unsupported")
        self.assertEqual(
            result.observation.evidence.descriptor()["type"],
            "verification-unsupported",
        )
        latest = self.stores.observed_state.latest(
            "workspace-a", "verification:api:semantic-http"
        )
        self.assertEqual(latest.observation_id, "verification-unsupported-a")

    def test_adapter_loss_leaves_immutable_intent_without_terminal_lie(self) -> None:
        service = VerificationCommandService(
            self.factory,
            VerificationInterpreterRegistry(
                {VerificationCapability.HTTP: RaisingHttpInterpreter()}
            ),
            clock=lambda: NOW,
            id_factory=_ids("verification-lost-intent-a"),
        )

        with self.assertRaisesRegex(OSError, "simulated transport loss"):
            service.execute(_command())

        history = self.stores.observed_state.history(
            "workspace-a", "verification:api:semantic-http"
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].status.value, "starting")
        self.assertEqual(history[0].probe_outcome.value, "unknown")

    def test_schema_reinstallation_preserves_verification_row_and_constraints(self) -> None:
        self.service.execute(_command())
        before = _constraint_oids(self.connection)

        install_schema(self.connection)
        install_schema(self.connection)

        self.assertEqual(_constraint_oids(self.connection), before)
        self.assertIsNotNone(
            self.stores.observed_state.latest(
                "workspace-a", "verification:api:semantic-http"
            )
        )

    def test_api_mcp_and_cli_expose_one_shared_redacted_projection(self) -> None:
        self.service.execute(_command())
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            observed_state_store=self.stores.observed_state,
            clock=lambda: NOW,
        )
        canonical = service.observed_state("workspace-a").descriptor()
        client = TestClient(create_instance_read_app(service))

        api = client.get("/workspaces/workspace-a/observed-state").json()
        mcp = ReadOnlyMcpAdapter(service).call_tool(
            "get_observed_state", {"workspace_id": "workspace-a"}
        )["content"][0]["json"]
        stdout = StringIO()
        status = run_cli(
            ["--base-url", "http://instance", "observed-state", "workspace-a"],
            opener=TestClientOpener(client),
            stdout=stdout,
            stderr=StringIO(),
            env={},
        )

        self.assertEqual(status, 0)
        self.assertEqual(api, canonical)
        self.assertEqual(mcp, canonical)
        self.assertEqual(json.loads(stdout.getvalue()), canonical)
        self.assertNotIn("http://api:8080", str((api, mcp, stdout.getvalue())))


def _command() -> ExecuteVerification:
    return ExecuteVerification(
        "workspace-a",
        _material(),
        VerificationAuthority(
            "operator", frozenset((VerificationScope.EXECUTE,))
        ),
    )


def _material() -> VerificationCheckMaterial:
    return VerificationCheckMaterial(
        "api",
        "graph-a",
        HttpCheck(
            check_id="semantic-http",
            provider_socket="internal",
            path="/verify",
        ),
        EndpointMaterial(
            "internal",
            Protocol.HTTP,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("http://api:8080"),
        ),
    )


def _graph(graph_id: str, workspace_id: str, version: int) -> GraphVersionRecord:
    return GraphVersionRecord(
        graph_id=graph_id,
        workspace_id=workspace_id,
        version=version,
        graph_descriptor={"name": graph_id},
        created_by="operator",
        created_at="2026-07-19T12:00:00Z",
    )


def _constraint_oids(connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT conname, oid
        FROM pg_constraint
        WHERE conrelid = 'cpk_observations'::regclass
        ORDER BY conname
        """
    ).fetchall()
    return dict(rows)


def _ids(*values: str):
    remaining = iter(values)
    return lambda: next(remaining)


class TestClientOpener:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def __call__(self, request):
        url = urlsplit(request.full_url)
        response = self._client.get(
            url.path + (f"?{url.query}" if url.query else ""),
            headers=dict(request.header_items()),
        )
        return TestClientResponse(response.content)


class TestClientResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


if __name__ == "__main__":
    unittest.main()
