from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import itertools
import json
import os
import socket
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
from fastapi.testclient import TestClient

from control_plane_kit import (
    IdempotencyGatewayPolicy,
    IdempotencyMethod,
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyRecordStatus,
    IdempotencyRoutePolicy,
    PackageServerProduct,
    idempotency_identity,
    idempotency_policy_from_descriptor,
)
from control_plane_kit.idempotency_gateway import (
    ExecuteIdempotentHttp,
    IdempotencyGatewayAuthority,
    IdempotencyGatewayDenied,
    IdempotencyGatewayScope,
    IdempotencyGatewayService,
    IdempotencyGatewayUnitOfWork,
    PostgresIdempotencyStore,
    install_idempotency_gateway_schema,
)
from control_plane_kit.servers import (
    HttpRequest,
    HttpResponse,
    ProductMaturity,
    create_idempotency_gateway_app,
    http_idempotency_gateway_block,
)
from control_plane_kit.stores import PostgresStoreBundle
from tests.postgres_case import PostgresStoreTestCase


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


@dataclass
class TransactionTracker:
    active: int = 0


class TrackedUnitOfWork:
    def __init__(self, delegate: IdempotencyGatewayUnitOfWork, tracker: TransactionTracker) -> None:
        self._delegate = delegate
        self._tracker = tracker

    @property
    def store(self):
        return self._delegate.store

    def __enter__(self):
        self._delegate.__enter__()
        self._tracker.active += 1
        return self

    def commit(self) -> None:
        self._delegate.commit()

    def __exit__(self, *args) -> None:
        try:
            self._delegate.__exit__(*args)
        finally:
            self._tracker.active -= 1


class IdempotencyGatewayTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database_url = os.environ["CPK_TEST_DATABASE_URL"]
        install_idempotency_gateway_schema(lambda: psycopg.connect(self.database_url))
        self.connection.execute("DELETE FROM cpk_idempotency_requests")
        self.connection.commit()
        self.tracker = TransactionTracker()
        self.ids = (f"idempotency-{value}" for value in itertools.count(1))
        self.calls: list[HttpRequest] = []

    def factory(self):
        return TrackedUnitOfWork(
            IdempotencyGatewayUnitOfWork(lambda: psycopg.connect(self.database_url)),
            self.tracker,
        )

    def service(self, target=None, *, clock=lambda: NOW):
        def default(request: HttpRequest) -> HttpResponse:
            self.assertEqual(self.tracker.active, 0)
            self.calls.append(request)
            return HttpResponse(201, {"location": "/orders/42"}, b"created-body")

        return IdempotencyGatewayService(
            self.factory,
            default if target is None else target,
            clock=clock,
            id_factory=lambda: next(self.ids),
        )

    def test_exact_replay_returns_reference_without_persisting_body_or_key(self) -> None:
        service = self.service()
        first = service.execute(_command())
        replay = service.execute(_command())

        self.assertIs(first.outcome, IdempotencyOutcome.EXECUTED)
        self.assertEqual(first.response.body, b"created-body")
        self.assertIs(replay.outcome, IdempotencyOutcome.REPLAYED)
        self.assertEqual(replay.response.status_code, 201)
        self.assertEqual(replay.response.body, b"")
        self.assertEqual(replay.response.headers["idempotency-result-reference"], "/orders/42")
        self.assertEqual(len(self.calls), 1)
        row = self.connection.execute(
            "SELECT row_to_json(value)::text FROM cpk_idempotency_requests value"
        ).fetchone()[0]
        self.assertNotIn("created-body", row)
        self.assertNotIn("operator-visible-key", row)
        self.assertNotIn("tenant-a", row)
        self.assertNotIn("operator-a", row)

    def test_same_key_with_changed_payload_conflicts_without_effect(self) -> None:
        service = self.service()
        service.execute(_command())
        conflict = service.execute(_command(body=b"different"))
        self.assertIs(conflict.outcome, IdempotencyOutcome.CONFLICT)
        self.assertEqual(conflict.response.status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_same_key_with_changed_query_conflicts_without_effect(self) -> None:
        service = self.service()
        service.execute(
            _command(request=HttpRequest("POST", "/orders", "region=east", body=b"payload"))
        )
        conflict = service.execute(
            _command(request=HttpRequest("POST", "/orders", "region=west", body=b"payload"))
        )

        self.assertIs(conflict.outcome, IdempotencyOutcome.CONFLICT)
        self.assertEqual(conflict.response.status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_authority_and_route_eligibility_fail_before_effect_or_write(self) -> None:
        service = self.service()
        denied = _command()
        denied = ExecuteIdempotentHttp(
            denied.gateway_id,
            denied.policy,
            denied.request,
            denied.idempotency_key,
            denied.tenant_identity,
            denied.actor_identity,
            IdempotencyGatewayAuthority("gateway", frozenset()),
        )
        with self.assertRaises(IdempotencyGatewayDenied):
            service.execute(denied)
        ineligible = service.execute(
            _command(request=HttpRequest("POST", "/unlisted", body=b"payload"))
        )
        self.assertIs(ineligible.outcome, IdempotencyOutcome.INELIGIBLE)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_idempotency_requests"
            ).fetchone()[0],
            0,
        )

    def test_same_key_with_changed_actor_conflicts_without_effect(self) -> None:
        service = self.service()
        service.execute(_command())
        conflict = service.execute(_command(actor="operator-b"))
        self.assertIs(conflict.outcome, IdempotencyOutcome.CONFLICT)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_identical_requests_have_one_execution_winner(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        target_calls = 0
        target_lock = threading.Lock()

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal target_calls
            self.assertEqual(self.tracker.active, 0)
            with target_lock:
                target_calls += 1
            entered.set()
            release.wait(timeout=5)
            return HttpResponse(201, {"location": "/orders/1"}, b"done")

        service = self.service(target)
        results = []
        first = threading.Thread(target=lambda: results.append(service.execute(_command())))
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second = service.execute(_command())
        release.set()
        first.join(timeout=5)

        self.assertEqual(target_calls, 1)
        self.assertIs(second.outcome, IdempotencyOutcome.IN_FLIGHT)
        self.assertIs(results[0].outcome, IdempotencyOutcome.EXECUTED)

    def test_effect_loss_is_uncertain_and_never_replays_automatically(self) -> None:
        calls = 0

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            raise OSError("effect connection lost after dispatch")

        service = self.service(target)
        first = service.execute(_command())
        second = service.execute(_command())
        self.assertIs(first.outcome, IdempotencyOutcome.UNCERTAIN)
        self.assertIs(second.outcome, IdempotencyOutcome.UNCERTAIN)
        self.assertEqual(calls, 1)
        self.assertIs(second.record.status, IdempotencyRecordStatus.UNCERTAIN)

    def test_result_persistence_loss_becomes_uncertain_after_lease_expiry(self) -> None:
        normal_factory = self.factory
        opens = 0

        def fail_second_open():
            nonlocal opens
            opens += 1
            if opens == 2:
                raise OSError("database unavailable after target completed")
            return normal_factory()

        service = IdempotencyGatewayService(
            fail_second_open,
            lambda request: self.calls.append(request)
            or HttpResponse(201, {"location": "/orders/lost"}, b"created"),
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )
        with self.assertRaises(OSError):
            service.execute(_command())

        recovered = self.service(clock=lambda: NOW + timedelta(seconds=6)).execute(
            _command()
        )
        self.assertIs(recovered.outcome, IdempotencyOutcome.UNCERTAIN)
        self.assertEqual(len(self.calls), 1)

    def test_expired_in_flight_record_becomes_uncertain_without_dispatch(self) -> None:
        policy = _policy()
        identity = idempotency_identity(
            gateway_id="gateway-a", key="operator-visible-key", tenant="tenant-a",
            actor="operator-a", method=IdempotencyMethod.POST, route="/orders",
            payload=b"payload", max_key_bytes=policy.max_key_bytes,
        )
        PostgresIdempotencyStore(self.connection).add(IdempotencyRecord(
            "expired", identity, IdempotencyRecordStatus.IN_FLIGHT,
            _timestamp(NOW - timedelta(minutes=2)), _timestamp(NOW + timedelta(days=1)),
            _timestamp(NOW - timedelta(minutes=1)),
        ))

        result = self.service().execute(_command())
        self.assertIs(result.outcome, IdempotencyOutcome.UNCERTAIN)
        self.assertEqual(self.calls, [])
        self.assertIs(result.record.status, IdempotencyRecordStatus.UNCERTAIN)

    def test_uncertain_records_are_not_evicted_to_make_capacity(self) -> None:
        policy = IdempotencyGatewayPolicy(
            (IdempotencyRoutePolicy("/orders", IdempotencyMethod.POST),),
            max_records=1,
        )
        failing = self.service(lambda _request: (_ for _ in ()).throw(OSError("loss")))
        failing.execute(_command(policy=policy))
        result = failing.execute(_command(policy=policy, key="second-key"))
        self.assertIs(result.outcome, IdempotencyOutcome.CAPACITY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute("SELECT status FROM cpk_idempotency_requests").fetchone()[0],
            "uncertain",
        )

    def test_expired_terminal_record_can_be_reused_as_fresh_intent(self) -> None:
        times = iter(
            (
                NOW,
                NOW,
                NOW + timedelta(seconds=61),
                NOW + timedelta(seconds=61),
            )
        )
        service = self.service(clock=lambda: next(times))
        first = service.execute(_command())
        second = service.execute(_command())
        self.assertIs(first.outcome, IdempotencyOutcome.EXECUTED)
        self.assertIs(second.outcome, IdempotencyOutcome.EXECUTED)
        self.assertNotEqual(first.record.request_id, second.record.request_id)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_idempotency_requests"
            ).fetchone()[0],
            1,
        )

    def test_gateway_unit_of_work_rolls_back_without_commit_request(self) -> None:
        identity = idempotency_identity(
            gateway_id="gateway-a",
            key="rollback-key",
            tenant="tenant-a",
            actor="operator-a",
            method=IdempotencyMethod.POST,
            route="/orders",
            payload=b"payload",
            max_key_bytes=256,
        )
        with IdempotencyGatewayUnitOfWork(
            lambda: psycopg.connect(self.database_url)
        ) as work:
            work.store.add(
                IdempotencyRecord(
                    "rolled-back",
                    identity,
                    IdempotencyRecordStatus.IN_FLIGHT,
                    _timestamp(NOW),
                    _timestamp(NOW + timedelta(minutes=1)),
                    _timestamp(NOW + timedelta(seconds=5)),
                )
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_idempotency_requests"
            ).fetchone()[0],
            0,
        )

    def test_oversized_result_reference_is_not_retained_for_replay(self) -> None:
        service = self.service(
            lambda _request: HttpResponse(201, {"location": "/" + "x" * 2_048}, b"ok")
        )
        first = service.execute(_command())
        replay = service.execute(_command())
        self.assertIs(first.outcome, IdempotencyOutcome.EXECUTED)
        self.assertIs(replay.outcome, IdempotencyOutcome.REPLAYED)
        self.assertNotIn("idempotency-result-reference", replay.response.headers)

    def test_schema_reinstall_preserves_rows_and_constraint_identity(self) -> None:
        self.service().execute(_command())
        before = self.connection.execute(
            "SELECT oid FROM pg_constraint WHERE conname = 'cpk_idempotency_status_check'"
        ).fetchone()[0]
        install_idempotency_gateway_schema(lambda: psycopg.connect(self.database_url))
        after = self.connection.execute(
            "SELECT oid FROM pg_constraint WHERE conname = 'cpk_idempotency_status_check'"
        ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_idempotency_requests").fetchone()[0], 1)

    def test_fastapi_boundary_requires_attestation_and_reconstructs_durable_replay(self) -> None:
        seen_headers = []

        def target(request: HttpRequest) -> HttpResponse:
            seen_headers.append({key.lower(): value for key, value in request.headers.items()})
            return HttpResponse(201, {"location": "/orders/http"}, b"created")

        service = self.service(target)
        app = create_idempotency_gateway_app(
            lambda request, key, tenant, actor: service.execute(
                _command(
                    request=request,
                    key=key,
                    tenant=tenant,
                    actor=actor,
                    gateway_id="gateway-http",
                )
            ).response,
            _policy(),
            identity_attestation_token="attestation-secret",
        )
        client = TestClient(app)
        headers = {
            "X-CPK-Identity-Attestation": "attestation-secret",
            "X-CPK-Authenticated-Tenant": "tenant-http",
            "X-CPK-Authenticated-Subject": "actor-http",
            "Idempotency-Key": "http-key",
        }

        self.assertEqual(client.post("/orders").status_code, 401)
        first = client.post("/orders", headers=headers, content=b"payload")
        replay = client.post("/orders", headers=headers, content=b"payload")
        self.assertEqual((first.status_code, first.content), (201, b"created"))
        self.assertEqual(first.headers["location"], "/orders/http")
        self.assertEqual((replay.status_code, replay.content), (201, b""))
        self.assertEqual(replay.headers["idempotency-result-reference"], "/orders/http")
        self.assertEqual(len(seen_headers), 1)
        self.assertNotIn("idempotency-key", seen_headers[0])
        self.assertNotIn("x-cpk-identity-attestation", seen_headers[0])

    def test_fastapi_boundary_rejects_oversized_body_before_service(self) -> None:
        calls = 0

        def execute(_request, _key, _tenant, _actor):
            nonlocal calls
            calls += 1
            return HttpResponse.text("must-not-run")

        policy = IdempotencyGatewayPolicy(
            (IdempotencyRoutePolicy("/orders", IdempotencyMethod.POST),),
            max_request_bytes=4,
        )
        client = TestClient(
            create_idempotency_gateway_app(
                execute,
                policy,
                identity_attestation_token="attestation-secret",
            )
        )
        response = client.post(
            "/orders",
            content=b"12345",
            headers={
                "X-CPK-Identity-Attestation": "attestation-secret",
                "X-CPK-Authenticated-Tenant": "tenant-http",
                "X-CPK-Authenticated-Subject": "actor-http",
                "Idempotency-Key": "http-key",
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(calls, 0)

    def test_policy_codec_and_block_preserve_closed_runtime_requirements(self) -> None:
        policy = _policy()
        self.assertEqual(idempotency_policy_from_descriptor(policy.descriptor()), policy)
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            idempotency_policy_from_descriptor({**policy.descriptor(), "escape": True})
        block = http_idempotency_gateway_block(policy=policy)
        self.assertIs(block.spec.product, PackageServerProduct.HTTP_IDEMPOTENCY_GATEWAY)
        self.assertIs(block.spec.maturity, ProductMaturity.TEST_ONLY)
        self.assertEqual(block.sockets.requirement_names(), ("target", "database"))
        self.assertEqual(block.sockets.provider_names(), ("internal",))
        command = " ".join(block.implementation.command)
        self.assertIn("idempotency_gateway.main", command)
        self.assertNotIn("attestation-secret", command)
        self.assertFalse(hasattr(PostgresStoreBundle(self.connection), "idempotency"))

    def test_live_process_bootstraps_own_store_and_replays_without_second_effect(self) -> None:
        target = _LiveTarget()
        target.start()
        self.addCleanup(target.stop)
        environment = dict(os.environ)
        environment.update(
            {
                "IDEMPOTENCY_DATABASE_URL": self.database_url,
                "IDEMPOTENCY_TARGET_URL": f"http://127.0.0.1:{target.port}",
                "CPK_IDEMPOTENCY_IDENTITY_TOKEN": "live-attestation",
                "CPK_IDEMPOTENCY_GATEWAY_ID": "live-gateway",
            }
        )
        process = subprocess.Popen(
            (
                "python",
                "-m",
                "control_plane_kit.idempotency_gateway.main",
                json.dumps(_policy().descriptor(), sort_keys=True),
            ),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_for_gateway()

        with self.assertRaises(HTTPError) as denied:
            urlopen(Request("http://127.0.0.1:8080/orders", data=b"payload", method="POST"))
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        headers = {
            "X-CPK-Identity-Attestation": "live-attestation",
            "X-CPK-Authenticated-Tenant": "tenant-live",
            "X-CPK-Authenticated-Subject": "actor-live",
            "Idempotency-Key": "live-key",
        }
        first = urlopen(Request("http://127.0.0.1:8080/orders", data=b"payload", headers=headers, method="POST"))
        with first:
            self.assertEqual((first.status, first.read()), (201, b"live-created"))
            self.assertEqual(first.headers["location"], "/orders/live")
        replay = urlopen(Request("http://127.0.0.1:8080/orders", data=b"payload", headers=headers, method="POST"))
        with replay:
            self.assertEqual((replay.status, replay.read()), (201, b""))
            self.assertEqual(replay.headers["idempotency-result-reference"], "/orders/live")
        self.assertEqual(target.calls, 1)
        durable = self.connection.execute(
            "SELECT row_to_json(value)::text FROM cpk_idempotency_requests value"
        ).fetchone()[0]
        self.assertNotIn("live-created", durable)
        self.assertNotIn("live-key", durable)
        self.assertNotIn("live-attestation", durable)


def _policy() -> IdempotencyGatewayPolicy:
    return IdempotencyGatewayPolicy(
        (IdempotencyRoutePolicy("/orders", IdempotencyMethod.POST),),
        retention_seconds=60,
        in_flight_lease_seconds=5,
        max_records=10,
    )


def _command(
    *,
    body: bytes = b"payload",
    key: str = "operator-visible-key",
    policy: IdempotencyGatewayPolicy | None = None,
    request: HttpRequest | None = None,
    tenant: str = "tenant-a",
    actor: str = "operator-a",
    gateway_id: str = "gateway-a",
) -> ExecuteIdempotentHttp:
    return ExecuteIdempotentHttp(
        gateway_id,
        _policy() if policy is None else policy,
        request or HttpRequest("POST", "/orders", "", {"Idempotency-Key": key}, body),
        key,
        tenant,
        actor,
        IdempotencyGatewayAuthority("gateway", frozenset((IdempotencyGatewayScope.EXECUTE,))),
    )


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class _LiveTargetHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.server.calls += 1
        body = b"live-created"
        self.send_response(201)
        self.send_header("location", "/orders/live")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _LiveTarget:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _LiveTargetHandler)
        self._server.calls = 0
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def calls(self) -> int:
        return self._server.calls

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _wait_for_gateway() -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 8080), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("idempotency gateway did not become reachable")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
