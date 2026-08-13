from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
import os
from types import SimpleNamespace
import unittest

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.gateway_delegation import (
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError,
    CpkServerReadService,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeAttempt,
    GatewayProbeAttemptStatus,
)
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.gateway_probe_store import GatewayProbeStore
from control_plane_kit_operations.read_pages import (
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageRequest,
    WorkspaceReadScope,
)
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.records import BoundedEvidence, WorkspaceRecord


EpochReadCursor = getattr(operations, "EpochReadCursor", None)


def _epoch_cursor(*args: object):
    if EpochReadCursor is None:
        raise AssertionError("EpochReadCursor is missing")
    return EpochReadCursor(*args)


def _page(store: object, request: ReadPageRequest):
    method = getattr(store, "page", None)
    if method is None:
        raise AssertionError("GatewayProbeStore.page is missing")
    return method(request)


class _Rows:
    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return ()


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        self.calls.append((query, parameters))
        return _Rows()


def _attempt(probe_id: str, epoch_second: int) -> GatewayProbeAttempt:
    return GatewayProbeAttempt(
        probe_id=probe_id,
        workspace_id="workspace-a",
        request_id=f"request-{probe_id}",
        actor_id="operator-a",
        current_graph_id="graph-current",
        gateway_node_id="gateway",
        gateway_runtime_id="docker-a",
        access_path=GatewayProbeAccessPath.RUNTIME_PRIVATE,
        probe_kind=GatewayProbeCommandKind.HTTP_STATUS,
        target_id="hello.http",
        request_digest="1" * 64,
        issuer="cpk-test",
        key_id="gateway-test-key",
        audience="gateway:workspace-a:gateway",
        grant_jti=f"grant-{probe_id}",
        issued_at=epoch_second,
        expires_at=epoch_second + 60,
        status=GatewayProbeAttemptStatus.INTENDED,
        requested_at="2026-08-12T12:00:00Z",
        intent_fingerprint="2" * 64,
        evidence=BoundedEvidence(),
    )


class GatewayProbePageSqlShapeTests(unittest.TestCase):
    def test_page_uses_exact_native_descending_tuple_seek_and_limit_plus_one(self) -> None:
        connection = _RecordingConnection()
        scope = WorkspaceReadScope("workspace-a")
        cursor = _epoch_cursor(
            ReadCollection.GATEWAY_PROBES,
            scope,
            9_007_199_254_740_993,
            "probe-a",
        )

        page = _page(GatewayProbeStore(connection),
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 2, cursor)
        )

        self.assertEqual(page.items, ())
        self.assertIsNone(page.next_cursor)
        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("(issued_at, probe_id) < (%s, %s)", normalized)
        self.assertIn("ORDER BY issued_at DESC, probe_id DESC", normalized)
        self.assertIn("LIMIT %s", normalized)
        for forbidden in ("OFFSET", "COUNT", "CAST", "timestamp", "to_timestamp"):
            self.assertNotIn(forbidden.lower(), normalized.lower())
        self.assertEqual(
            parameters,
            ("workspace-a", 9_007_199_254_740_993, "probe-a", 3),
        )

    def test_first_page_is_one_bounded_query_without_seek_or_count(self) -> None:
        connection = _RecordingConnection()
        scope = WorkspaceReadScope("workspace-a")

        _page(GatewayProbeStore(connection),
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 100)
        )

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertNotIn("(issued_at, probe_id) <", normalized)
        self.assertNotIn("OFFSET", normalized)
        self.assertNotIn("COUNT", normalized)
        self.assertEqual(parameters, ("workspace-a", 101))


class GatewayProbePostgresPageTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions (
              graph_id, workspace_id, version, graph_descriptor, created_by, created_at
            ) VALUES (
              'graph-current', 'workspace-a', 1, '{}'::jsonb,
              'operator-a', '2026-08-12T12:00:00Z'
            )
            """
        )
        self.store = GatewayProbeStore(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_empty_final_and_equal_second_pages_follow_descending_identity(self) -> None:
        scope = WorkspaceReadScope("workspace-a")
        empty = _page(self.store,
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 2)
        )
        self.assertEqual(empty.items, ())
        self.assertIsNone(empty.next_cursor)

        for probe_id, epoch_second in (
            ("probe-z", 300),
            ("probe-b", 200),
            ("probe-a", 200),
            ("probe-c", 100),
        ):
            self.store.add(_attempt(probe_id, epoch_second))

        first = _page(self.store,
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 2)
        )
        second = _page(self.store,
            ReadPageRequest(
                ReadCollection.GATEWAY_PROBES,
                scope,
                2,
                first.next_cursor,
            )
        )

        self.assertEqual([item.probe_id for item in first.items], ["probe-z", "probe-b"])
        self.assertEqual([item.probe_id for item in second.items], ["probe-a", "probe-c"])
        self.assertIsNone(second.next_cursor)
        self.assertEqual(first.next_cursor.epoch_second, 200)
        self.assertEqual(first.next_cursor.item_id, "probe-b")

    def test_committed_new_head_requires_fresh_traversal_and_new_tail_may_appear(self) -> None:
        scope = WorkspaceReadScope("workspace-a")
        for probe_id, epoch_second in (
            ("probe-z", 300),
            ("probe-b", 200),
            ("probe-a", 200),
            ("probe-c", 100),
        ):
            self.store.add(_attempt(probe_id, epoch_second))
        first = _page(self.store,
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 2)
        )

        with psycopg.connect(self.database_url) as writer:
            store = GatewayProbeStore(writer)
            store.add(_attempt("probe-new-head", 400))
            store.add(_attempt("probe-new-tail", 150))

        continued = _page(self.store,
            ReadPageRequest(
                ReadCollection.GATEWAY_PROBES,
                scope,
                10,
                first.next_cursor,
            )
        )
        fresh = _page(self.store,
            ReadPageRequest(ReadCollection.GATEWAY_PROBES, scope, 10)
        )

        first_ids = [item.probe_id for item in first.items]
        continued_ids = [item.probe_id for item in continued.items]
        self.assertEqual(first_ids, ["probe-z", "probe-b"])
        self.assertEqual(continued_ids, ["probe-a", "probe-new-tail", "probe-c"])
        self.assertTrue(set(first_ids).isdisjoint(continued_ids))
        self.assertNotIn("probe-new-head", continued_ids)
        self.assertEqual(fresh.items[0].probe_id, "probe-new-head")


class _WorkspaceStore:
    def get(self, workspace_id: str) -> WorkspaceRecord:
        if workspace_id != "workspace-a":
            raise KeyError(workspace_id)
        return WorkspaceRecord("workspace-a", "Workspace A")


class _ProbePageStore:
    def __init__(self) -> None:
        self.items = (_attempt("probe-b", 200), _attempt("probe-a", 100))
        self.requests: list[ReadPageRequest] = []

    def page(self, request: ReadPageRequest) -> ReadPage[GatewayProbeAttempt]:
        self.requests.append(request)
        candidates = tuple(
            ReadPageCandidate(
                item,
                _epoch_cursor(
                    ReadCollection.GATEWAY_PROBES,
                    request.scope,
                    item.issued_at,
                    item.probe_id,
                ),
            )
            for item in self.items
        )
        if request.cursor is None:
            selected = candidates
        else:
            self.assert_cursor(request.cursor, candidates[0].cursor_after_item)
            selected = candidates[1:]
        return ReadPage.from_candidates(request, selected)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GatewayProbeAttempt, ...]:
        if workspace_id != "workspace-a":
            raise AssertionError("route selected the wrong workspace")
        return self.items[offset : offset + limit]

    def count_for_workspace(self, workspace_id: str) -> int:
        if workspace_id != "workspace-a":
            raise AssertionError("route selected the wrong workspace")
        return len(self.items)

    @staticmethod
    def assert_cursor(actual: object, expected: object) -> None:
        if actual != expected:
            raise AssertionError("route decoded the wrong gateway probe cursor")

    def get(self, probe_id: str) -> GatewayProbeAttempt:
        for item in self.items:
            if item.probe_id == probe_id:
                return item
        raise KeyError(probe_id)


class _UnitOfWork:
    def __init__(self, stores: object) -> None:
        self.stores = stores

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def commit(self) -> None:
        return None


def _principal(*, authorized: bool = True) -> AuthenticatedPrincipal:
    grants = (
        (WorkspaceGrant("workspace-a", tuple(PolicyScope)),)
        if authorized
        else ()
    )
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:gateway-probe-pages",
            subject_id="operator-a",
            kind=PrincipalKind.OPERATOR,
        ),
        grants,
    )


@dataclass(frozen=True)
class _RouteRequest:
    surface: str = "http"
    route_id: str = "read.gateway-probe-timeline"
    service_role: ControlPlaneServiceRole = ControlPlaneServiceRole.READS
    path_parameters: dict[str, str] = field(
        default_factory=lambda: {"workspace_id": "workspace-a"}
    )
    payload: dict[str, object] = field(default_factory=lambda: {"limit": 1})
    principal: AuthenticatedPrincipal = field(default_factory=_principal)


def _read_service(store: _ProbePageStore) -> CpkServerReadService:
    stores = SimpleNamespace(
        workspaces=_WorkspaceStore(),
        graphs=object(),
        activity_history=object(),
        execution=object(),
        observed_state=object(),
        runtime_authorities=object(),
        runtime_authority_deliveries=object(),
        ingress_authorities=object(),
        secret_providers=object(),
        secret_references=object(),
        gateway_probes=store,
        delegation_signing_keys=object(),
    )
    return CpkServerReadService(lambda: _UnitOfWork(stores))


class GatewayProbeReadServiceAndAdapterTests(unittest.TestCase):
    def test_service_owns_only_the_page_selector_and_common_envelope(self) -> None:
        store = _ProbePageStore()
        request = ReadPageRequest(
            ReadCollection.GATEWAY_PROBES,
            WorkspaceReadScope("workspace-a"),
            1,
        )

        method = InstanceReadService.gateway_probe_timeline
        self.assertEqual(tuple(signature(method).parameters), ("self", "request"))
        if tuple(signature(method).parameters) != ("self", "request"):
            return
        page = InstanceReadService(
            workspace_store=_WorkspaceStore(),
            graph_topology_store=object(),
            gateway_probe_store=store,
        ).gateway_probe_timeline(request)

        self.assertEqual(len(store.requests), 1)
        self.assertEqual(page.request, request)
        descriptor = page.descriptor()
        self.assertEqual(
            set(descriptor),
            {"workspace_id", "kind", "limit", "items", "next_cursor"},
        )
        self.assertNotIn("offset", descriptor)
        self.assertNotIn("total", descriptor)
        self.assertNotIn("has_more", descriptor)

    def test_http_and_mcp_pages_are_identical_and_detail_is_unchanged(self) -> None:
        service = _read_service(_ProbePageStore())
        http = service.handle(_RouteRequest())
        mcp = service.handle(
            _RouteRequest(
                surface="mcp",
                path_parameters={},
                payload={"workspace_id": "workspace-a", "limit": 1},
            )
        )
        self.assertEqual(http, mcp)
        self.assertIn("next_cursor", http)
        if "next_cursor" not in http:
            return
        self.assertIsNotNone(http["next_cursor"])

        http_after = service.handle(
            _RouteRequest(payload={"limit": 1, "after": http["next_cursor"]})
        )
        mcp_after = service.handle(
            _RouteRequest(
                surface="mcp",
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "limit": 1,
                    "after": mcp["next_cursor"],
                },
            )
        )
        self.assertEqual(http_after, mcp_after)
        self.assertIsNone(http_after["next_cursor"])

        detail = service.handle(
            _RouteRequest(
                route_id="read.gateway-probe-detail",
                path_parameters={
                    "workspace_id": "workspace-a",
                    "probe_id": "probe-b",
                },
                payload={},
            )
        )
        self.assertEqual(detail["kind"], "gateway-probe-detail")
        self.assertEqual(detail["gateway_probe"], _attempt("probe-b", 200).descriptor())

    def test_stale_offset_unknown_and_oversized_page_arguments_are_rejected(self) -> None:
        service = _read_service(_ProbePageStore())
        requests = (
            _RouteRequest(payload={"offset": 1}),
            _RouteRequest(payload={"unknown": "candidate-do-not-retain"}),
            _RouteRequest(payload={"limit": 101}),
            _RouteRequest(
                surface="mcp",
                path_parameters={},
                payload={"workspace_id": "workspace-a", "offset": 1},
            ),
        )
        for request in requests:
            with self.subTest(surface=request.surface, payload=request.payload):
                with self.assertRaises(CpkServerApplicationError) as caught:
                    service.handle(request)
                self.assertEqual(caught.exception.status, 400)
                self.assertNotIn("candidate-do-not-retain", str(caught.exception))

    def test_authorized_bad_epoch_cursor_is_cause_free_before_uow(self) -> None:
        calls: list[str] = []

        def factory() -> object:
            calls.append("uow")
            raise AssertionError("malformed cursor must not acquire a UoW")

        malformed = _epoch_cursor(
            ReadCollection.GATEWAY_PROBES,
            WorkspaceReadScope("workspace-a"),
            1,
            "probe-a",
        ).descriptor()
        malformed["position"]["epoch_second"] = "01"
        with self.assertRaises(CpkServerApplicationError) as caught:
            CpkServerReadService(factory).handle(
                _RouteRequest(payload={"after": malformed})
            )

        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(calls, [])
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_unauthorized_request_fails_before_cursor_interpretation_and_uow(self) -> None:
        calls: list[str] = []

        def factory() -> object:
            calls.append("uow")
            raise AssertionError("unauthorized request must not acquire a UoW")

        with self.assertRaises(CpkServerApplicationError) as caught:
            CpkServerReadService(factory).handle(
                _RouteRequest(
                    payload={"after": {"candidate-do-not-retain": object()}},
                    principal=_principal(authorized=False),
                )
            )

        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(calls, [])
        self.assertNotIn("candidate-do-not-retain", str(caught.exception))


class GatewayProbeRetirementContractTests(unittest.TestCase):
    def test_offset_collection_wrapper_and_count_selectors_are_retired(self) -> None:
        from control_plane_kit_operations import read_services

        self.assertFalse(hasattr(read_services, "FocusedCollectionReadModel"))
        self.assertNotIn("FocusedCollectionReadModel", operations.__all__)
        self.assertFalse(hasattr(GatewayProbeStore, "list_for_workspace"))
        self.assertFalse(hasattr(GatewayProbeStore, "count_for_workspace"))


if __name__ == "__main__":
    unittest.main()
