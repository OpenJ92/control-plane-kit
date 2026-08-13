from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Callable, Iterator
import unittest
import uuid

import psycopg

from tests.large_read_history_fixture import (
    LargeReadHistoryHandles,
    seed_large_read_history,
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
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.read_pages import (
    READ_COLLECTION_SPECS,
    PlanReadScope,
    ReadCollection,
    ReadPageRequest,
    ReadScope,
    RunReadScope,
    SessionReadScope,
    WorkspaceReadScope,
)
from control_plane_kit_operations.read_services import InstanceReadService


_LIMIT = 100
_CLOCK = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
_HOSTILE_CURSOR = {"api_token": "do-not-disclose"}


@dataclass(frozen=True, slots=True)
class _RouteCase:
    route_id: str
    collection: ReadCollection
    direct_method: str
    scope: Callable[[LargeReadHistoryHandles], ReadScope]
    http_path: Callable[[LargeReadHistoryHandles], dict[str, str]]
    mcp_payload: Callable[[LargeReadHistoryHandles], dict[str, str]]


@dataclass(frozen=True)
class _RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal


def _route_cases() -> tuple[_RouteCase, ...]:
    return (
        _RouteCase(
            "read.activity",
            ReadCollection.ACTIVITY_SESSIONS,
            "activity_sessions",
            lambda h: WorkspaceReadScope(h.activity_workspace_id),
            lambda h: {"workspace_id": h.activity_workspace_id},
            lambda h: {"workspace_id": h.activity_workspace_id},
        ),
        _RouteCase(
            "read.sessions",
            ReadCollection.OPEN_SESSIONS,
            "open_sessions",
            lambda h: WorkspaceReadScope(h.open_workspace_id),
            lambda h: {"workspace_id": h.open_workspace_id},
            lambda h: {"workspace_id": h.open_workspace_id},
        ),
        _RouteCase(
            "read.session-actions",
            ReadCollection.SESSION_ACTIONS,
            "session_actions",
            lambda h: SessionReadScope(
                h.actions_workspace_id,
                h.actions_session_id,
            ),
            lambda h: {
                "workspace_id": h.actions_workspace_id,
                "session_id": h.actions_session_id,
            },
            lambda h: {
                "workspace_id": h.actions_workspace_id,
                "session_id": h.actions_session_id,
            },
        ),
        _RouteCase(
            "read.session-plans",
            ReadCollection.SESSION_PLANS,
            "session_plans",
            lambda h: SessionReadScope(
                h.plans_workspace_id,
                h.plans_session_id,
            ),
            lambda h: {
                "workspace_id": h.plans_workspace_id,
                "session_id": h.plans_session_id,
            },
            lambda h: {
                "workspace_id": h.plans_workspace_id,
                "session_id": h.plans_session_id,
            },
        ),
        _RouteCase(
            "read.session-approvals",
            ReadCollection.SESSION_APPROVALS,
            "session_approvals",
            lambda h: SessionReadScope(
                h.approvals_workspace_id,
                h.approvals_session_id,
            ),
            lambda h: {
                "workspace_id": h.approvals_workspace_id,
                "session_id": h.approvals_session_id,
            },
            lambda h: {
                "workspace_id": h.approvals_workspace_id,
                "session_id": h.approvals_session_id,
            },
        ),
        _RouteCase(
            "read.pending-approvals",
            ReadCollection.PENDING_APPROVALS,
            "pending_approvals",
            lambda h: WorkspaceReadScope(h.pending_workspace_id),
            lambda h: {"workspace_id": h.pending_workspace_id},
            lambda h: {"workspace_id": h.pending_workspace_id},
        ),
        _RouteCase(
            "read.plan-runs",
            ReadCollection.PLAN_RUNS,
            "plan_runs",
            lambda h: PlanReadScope(h.runs_workspace_id, h.runs_plan_id),
            lambda h: {
                "workspace_id": h.runs_workspace_id,
                "plan_id": h.runs_plan_id,
            },
            lambda h: {
                "workspace_id": h.runs_workspace_id,
                "plan_id": h.runs_plan_id,
            },
        ),
        _RouteCase(
            "read.run-events",
            ReadCollection.RUN_EVENTS,
            "run_events",
            lambda h: RunReadScope(h.events_workspace_id, h.events_run_id),
            lambda h: {
                "workspace_id": h.events_workspace_id,
                "run_id": h.events_run_id,
            },
            lambda h: {
                "workspace_id": h.events_workspace_id,
                "run_id": h.events_run_id,
            },
        ),
        _RouteCase(
            "read.observed-state",
            ReadCollection.LATEST_OBSERVATIONS,
            "observed_state",
            lambda h: WorkspaceReadScope(h.observations_workspace_id),
            lambda h: {"workspace_id": h.observations_workspace_id},
            lambda h: {"workspace_id": h.observations_workspace_id},
        ),
        _RouteCase(
            "read.runtime-authorities",
            ReadCollection.RUNTIME_AUTHORITIES,
            "runtime_authorities",
            lambda h: WorkspaceReadScope(h.runtime_authorities_workspace_id),
            lambda h: {"workspace_id": h.runtime_authorities_workspace_id},
            lambda h: {"workspace_id": h.runtime_authorities_workspace_id},
        ),
        _RouteCase(
            "read.runtime-authority-deliveries",
            ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
            "runtime_authority_deliveries",
            lambda h: WorkspaceReadScope(h.runtime_deliveries_workspace_id),
            lambda h: {"workspace_id": h.runtime_deliveries_workspace_id},
            lambda h: {"workspace_id": h.runtime_deliveries_workspace_id},
        ),
        _RouteCase(
            "read.ingress-authorities",
            ReadCollection.INGRESS_AUTHORITIES,
            "ingress_authorities",
            lambda h: WorkspaceReadScope(h.ingress_authorities_workspace_id),
            lambda h: {"workspace_id": h.ingress_authorities_workspace_id},
            lambda h: {"workspace_id": h.ingress_authorities_workspace_id},
        ),
        _RouteCase(
            "read.secret-providers",
            ReadCollection.SECRET_PROVIDERS,
            "secret_providers",
            lambda h: WorkspaceReadScope(h.secret_providers_workspace_id),
            lambda h: {"workspace_id": h.secret_providers_workspace_id},
            lambda h: {"workspace_id": h.secret_providers_workspace_id},
        ),
        _RouteCase(
            "read.secret-references",
            ReadCollection.SECRET_REFERENCES,
            "secret_references",
            lambda h: WorkspaceReadScope(h.secret_references_workspace_id),
            lambda h: {"workspace_id": h.secret_references_workspace_id},
            lambda h: {"workspace_id": h.secret_references_workspace_id},
        ),
        _RouteCase(
            "read.delegation-keys",
            ReadCollection.DELEGATION_SIGNING_KEYS,
            "delegation_signing_keys",
            lambda h: WorkspaceReadScope(h.delegation_keys_workspace_id),
            lambda h: {"workspace_id": h.delegation_keys_workspace_id},
            lambda h: {"workspace_id": h.delegation_keys_workspace_id},
        ),
        _RouteCase(
            "read.gateway-probe-timeline",
            ReadCollection.GATEWAY_PROBES,
            "gateway_probe_timeline",
            lambda h: WorkspaceReadScope(h.gateway_probes_workspace_id),
            lambda h: {"workspace_id": h.gateway_probes_workspace_id},
            lambda h: {"workspace_id": h.gateway_probes_workspace_id},
        ),
    )


class LargeReadRouteParityTests(unittest.TestCase):
    @contextmanager
    def _seeded(
        self,
    ) -> Iterator[
        tuple[Callable[[], object], LargeReadHistoryHandles]
    ]:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        schema = f"large_read_routes_{uuid.uuid4().hex}"
        administration = psycopg.connect(database_url, autocommit=True)
        try:
            administration.execute(f'CREATE SCHEMA "{schema}"')
            administration.execute(f'SET search_path TO "{schema}"')
            install_schema(administration)
            handles = seed_large_read_history(administration, selected_count=201)

            def connect():
                connection = psycopg.connect(database_url)
                connection.execute(f'SET search_path TO "{schema}"')
                return connection

            yield connect, handles
        finally:
            administration.execute("SET search_path TO public")
            administration.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            administration.close()

    def test_literal_route_inventory_matches_public_collection_specs(self) -> None:
        cases = _route_cases()

        self.assertEqual(len(cases), 16)
        self.assertEqual(len({case.route_id for case in cases}), 16)
        self.assertEqual(len({case.collection for case in cases}), 16)
        self.assertEqual(len({case.direct_method for case in cases}), 16)
        self.assertEqual(
            {(case.route_id, case.collection) for case in cases},
            {(spec.route_id, spec.collection) for spec in READ_COLLECTION_SPECS},
        )
        for case in cases:
            self.assertTrue(callable(getattr(InstanceReadService, case.direct_method)))

    def test_direct_http_and_mcp_pages_have_exact_projection_parity(self) -> None:
        with self._seeded() as (connect, handles):
            route_service = CpkServerReadService(
                lambda: PostgresUnitOfWork(connect),
                clock=lambda: _CLOCK,
            )
            first_descriptors: dict[ReadCollection, dict[str, object]] = {}
            for case in _route_cases():
                with self.subTest(route_id=case.route_id):
                    scope = case.scope(handles)
                    direct_page, direct_first = self._direct_page(
                        connect,
                        case,
                        ReadPageRequest(case.collection, scope, _LIMIT),
                    )
                    http_first = route_service.handle(
                        self._request(case, handles, "http")
                    )
                    mcp_first = route_service.handle(
                        self._request(case, handles, "mcp")
                    )

                    self.assertEqual(http_first, direct_first)
                    self.assertEqual(mcp_first, direct_first)
                    self.assertIsNotNone(direct_page.next_cursor)
                    self.assertIsNotNone(http_first["next_cursor"])
                    self.assertIsNotNone(mcp_first["next_cursor"])
                    first_descriptors[case.collection] = direct_first

                    _, direct_second = self._direct_page(
                        connect,
                        case,
                        ReadPageRequest(
                            case.collection,
                            scope,
                            _LIMIT,
                            direct_page.next_cursor,
                        ),
                    )
                    http_second = route_service.handle(
                        self._request(
                            case,
                            handles,
                            "http",
                            after=http_first["next_cursor"],
                        )
                    )
                    mcp_second = route_service.handle(
                        self._request(
                            case,
                            handles,
                            "mcp",
                            after=mcp_first["next_cursor"],
                        )
                    )

                    self.assertEqual(http_second, direct_second)
                    self.assertEqual(mcp_second, direct_second)

            self._assert_projection_canaries(first_descriptors)

    def test_workspace_denial_precedes_cursor_and_store_for_every_route(self) -> None:
        handles = self._synthetic_handles()
        entered = 0

        def forbidden_unit_of_work():
            nonlocal entered
            entered += 1
            raise AssertionError("workspace denial must precede unit-of-work entry")

        service = CpkServerReadService(forbidden_unit_of_work, clock=lambda: _CLOCK)
        for case in _route_cases():
            for surface in ("http", "mcp"):
                with self.subTest(route_id=case.route_id, surface=surface):
                    workspace_id = case.scope(handles).workspace_id
                    principal = _operator(
                        "foreign-workspace",
                        scopes=tuple(PolicyScope),
                    )
                    with self.assertRaises(CpkServerApplicationError) as raised:
                        service.handle(
                            self._request(
                                case,
                                handles,
                                surface,
                                after=_HOSTILE_CURSOR,
                                principal=principal,
                            )
                        )

                    self.assertEqual(raised.exception.status, 403)
                    self.assertEqual(
                        str(raised.exception),
                        "workspace access is denied",
                    )
                    representation = repr(raised.exception)
                    self.assertNotIn(workspace_id, representation)
                    self.assertNotIn("foreign-workspace", representation)
                    self.assertNotIn("api_token", representation)
                    self.assertNotIn("do-not-disclose", representation)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
        self.assertEqual(entered, 0)

    @staticmethod
    def _direct_page(connect, case, request):
        with PostgresUnitOfWork(connect) as unit_of_work:
            service = _read_service(unit_of_work.stores)
            page = getattr(service, case.direct_method)(request)
            descriptor = page.descriptor()
            unit_of_work.commit()
            return page, descriptor

    @staticmethod
    def _request(
        case: _RouteCase,
        handles: LargeReadHistoryHandles,
        surface: str,
        *,
        after: object | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> _RouteRequest:
        if surface == "http":
            path_parameters = case.http_path(handles)
            payload: dict[str, object] = {"limit": _LIMIT}
        else:
            path_parameters = {}
            payload = {**case.mcp_payload(handles), "limit": _LIMIT}
        if after is not None:
            payload["after"] = after
        workspace_id = case.scope(handles).workspace_id
        return _RouteRequest(
            surface=surface,
            route_id=case.route_id,
            service_role=ControlPlaneServiceRole.READS,
            path_parameters=path_parameters,
            payload=payload,
            principal=principal or _operator(workspace_id),
        )

    def _assert_projection_canaries(
        self,
        descriptors: dict[ReadCollection, dict[str, object]],
    ) -> None:
        provider = descriptors[ReadCollection.SECRET_PROVIDERS]["items"][0]
        self.assertEqual(provider["endpoint_reference"], "synthetic-endpoint-0001")
        self.assertEqual(
            provider["credential_reference"],
            "secret://synthetic/provider/credential-1",
        )
        self.assertEqual(
            provider["allowed_reference_prefixes"],
            ["secret://provider-0001/workspace"],
        )

        reference = descriptors[ReadCollection.SECRET_REFERENCES]["items"][0]
        self.assertEqual(
            reference["reference_id"],
            "secret://synthetic/reference/value-1",
        )

        private_candidates = {
            ReadCollection.INGRESS_AUTHORITIES: (
                "secret://synthetic/cloudflare/token",
                "synthetic-provider",
                "secret://synthetic/ingress",
            ),
            ReadCollection.DELEGATION_SIGNING_KEYS: (
                "private_key_reference",
                "public_key_pem",
                "BEGIN PUBLIC KEY",
                "secret://synthetic/delegation/private-",
            ),
        }
        for collection, candidates in private_candidates.items():
            representation = repr(descriptors[collection])
            for candidate in candidates:
                with self.subTest(collection=collection.value, candidate=candidate):
                    self.assertNotIn(candidate, representation)

    @staticmethod
    def _synthetic_handles() -> LargeReadHistoryHandles:
        values = {}
        for name in LargeReadHistoryHandles.__dataclass_fields__:
            values[name] = f"candidate-{name}"
        return LargeReadHistoryHandles(**values)


def _read_service(stores) -> InstanceReadService:
    return InstanceReadService(
        workspace_store=stores.workspaces,
        graph_topology_store=stores.graphs,
        activity_history_store=stores.activity_history,
        execution_store=stores.execution,
        observed_state_store=stores.observed_state,
        runtime_authority_store=stores.runtime_authorities,
        runtime_authority_delivery_store=stores.runtime_authority_deliveries,
        ingress_authority_store=stores.ingress_authorities,
        secret_provider_store=stores.secret_providers,
        secret_reference_store=stores.secret_references,
        gateway_probe_store=stores.gateway_probes,
        delegation_signing_key_store=stores.delegation_signing_keys,
        clock=lambda: _CLOCK,
    )


def _operator(
    workspace_id: str,
    *,
    scopes: tuple[PolicyScope, ...] = tuple(PolicyScope),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:large-read-route-parity",
            subject_id="operator",
            kind=PrincipalKind.OPERATOR,
        ),
        (WorkspaceGrant(workspace_id, scopes),),
    )


if __name__ == "__main__":
    unittest.main()
