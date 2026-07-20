"""Bounded authenticated HTTP load-generator ApplicationBlock."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
import hmac
import json
import threading
import time
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_plane_kit.core.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.core.environment import PublicStaticEnvironmentBinding
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.domains.load_generation import (
    LoadGeneratorPolicy,
    LoadRequestOutcome,
    LoadRunCommand,
    LoadRunEvidence,
    LoadRunRecord,
    LoadRunStatus,
    load_run_command_from_descriptor,
    scheduled_offsets_ms,
    validate_load_command,
)
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


LoadTarget = Callable[[HttpRequest, int, int], HttpResponse]


class LoadRunConflict(RuntimeError):
    pass


class LoadGeneratorCapacityExhausted(RuntimeError):
    pass


@dataclass
class _MutableRun:
    command: LoadRunCommand
    status: LoadRunStatus
    evidence: LoadRunEvidence
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def record(self) -> LoadRunRecord:
        return LoadRunRecord(self.command, self.status, self.evidence)


class HttpLoadGeneratorServer:
    """Process-local interpreter for one bounded load program at a time."""

    def __init__(
        self,
        policy: LoadGeneratorPolicy,
        target: LoadTarget,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(policy, LoadGeneratorPolicy):
            raise TypeError("load generator requires a typed policy")
        self._policy = policy
        self._target = target
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._runs: dict[str, _MutableRun] = {}
        self._closed = False

    def trigger(self, command: LoadRunCommand) -> tuple[LoadRunRecord, bool]:
        validate_load_command(self._policy, command)
        with self._lock:
            if self._closed:
                raise RuntimeError("load generator is shut down")
            existing = self._runs.get(command.run_id)
            if existing is not None:
                if existing.command.fingerprint != command.fingerprint:
                    raise LoadRunConflict("load run id is already bound to different intent")
                return existing.record(), True
            if any(run.status is LoadRunStatus.RUNNING for run in self._runs.values()):
                raise LoadGeneratorCapacityExhausted("one bounded load run is already active")
            self._evict_terminal_runs()
            if len(self._runs) >= self._policy.max_retained_runs:
                raise LoadGeneratorCapacityExhausted("load-generator retained-run capacity is exhausted")
            run = _MutableRun(
                command,
                LoadRunStatus.RUNNING,
                LoadRunEvidence(planned=command.request_count),
            )
            thread = threading.Thread(
                target=self._execute,
                args=(run,),
                name=f"cpk-load-{command.run_id}",
                daemon=False,
            )
            run.thread = thread
            self._runs[command.run_id] = run
            thread.start()
            return run.record(), False

    def read(self, run_id: str) -> LoadRunRecord:
        with self._lock:
            try:
                return self._runs[run_id].record()
            except KeyError as exc:
                raise KeyError(f"unknown load run {run_id!r}") from exc

    def cancel(self, run_id: str) -> LoadRunRecord:
        with self._lock:
            try:
                run = self._runs[run_id]
            except KeyError as exc:
                raise KeyError(f"unknown load run {run_id!r}") from exc
            run.cancel.set()
            return run.record()

    def wait(self, run_id: str, timeout: float = 5.0) -> LoadRunRecord:
        with self._lock:
            run = self._runs[run_id]
            thread = run.thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError(f"load run {run_id!r} did not settle")
        return self.read(run_id)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            runs = tuple(self._runs.values())
            for run in runs:
                run.cancel.set()
            threads = tuple(run.thread for run in runs if run.thread is not None)
        for thread in threads:
            thread.join(timeout=(2 * self._policy.max_duration_ms / 1_000) + 1)

    def _execute(self, run: _MutableRun) -> None:
        command = run.command
        offsets = scheduled_offsets_ms(command)
        statically_deadline_skipped = command.request_count - len(offsets)
        start = self._clock()
        deadline_reached = False
        futures: set[Future[LoadRequestOutcome]] = set()
        executor = ThreadPoolExecutor(max_workers=command.concurrency, thread_name_prefix="cpk-load-request")
        try:
            for offset_ms in offsets:
                if run.cancel.is_set():
                    break
                while len(futures) >= command.concurrency:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                    self._record_outcomes(run, completed)
                    if run.cancel.is_set():
                        break
                if run.cancel.is_set():
                    break
                target_time = start + offset_ms / 1_000
                delay = target_time - self._clock()
                if delay > 0:
                    self._sleeper(delay)
                if run.cancel.is_set():
                    break
                if self._clock() - start >= command.duration_ms / 1_000:
                    deadline_reached = True
                    break
                futures.add(executor.submit(self._dispatch, command))
                self._increment_dispatched(run)
            while futures:
                completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                self._record_outcomes(run, completed)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            with self._lock:
                skipped = command.request_count - run.evidence.dispatched
                deadline_skipped = (
                    statically_deadline_skipped
                    if run.cancel.is_set()
                    else skipped if deadline_reached else statically_deadline_skipped
                )
                cancelled = max(0, skipped - deadline_skipped) if run.cancel.is_set() else 0
                run.evidence = replace(
                    run.evidence,
                    cancelled_before_dispatch=cancelled,
                    deadline_skipped=deadline_skipped,
                )
                if run.cancel.is_set():
                    run.status = LoadRunStatus.CANCELLED
                elif deadline_skipped:
                    run.status = LoadRunStatus.DEADLINE_REACHED
                else:
                    run.status = LoadRunStatus.COMPLETED

    def _dispatch(self, command: LoadRunCommand) -> LoadRequestOutcome:
        try:
            response = self._target(
                HttpRequest(command.method.value, command.path),
                command.timeout_ms,
                self._policy.max_response_bytes,
            )
        except TimeoutError:
            return LoadRequestOutcome.TIMED_OUT
        except Exception:  # noqa: BLE001 - target loss is a closed aggregate outcome.
            return LoadRequestOutcome.FAILED
        if 200 <= response.status_code < 400:
            return LoadRequestOutcome.SUCCEEDED
        if response.status_code == 429:
            return LoadRequestOutcome.REJECTED
        return LoadRequestOutcome.FAILED

    def _increment_dispatched(self, run: _MutableRun) -> None:
        with self._lock:
            run.evidence = replace(run.evidence, dispatched=run.evidence.dispatched + 1)

    def _record_outcomes(self, run: _MutableRun, futures: set[Future[LoadRequestOutcome]]) -> None:
        with self._lock:
            evidence = run.evidence
            counts = {
                LoadRequestOutcome.SUCCEEDED: evidence.succeeded,
                LoadRequestOutcome.REJECTED: evidence.rejected,
                LoadRequestOutcome.TIMED_OUT: evidence.timed_out,
                LoadRequestOutcome.FAILED: evidence.failed,
            }
            for future in futures:
                outcome = future.result()
                counts[outcome] += 1
            run.evidence = replace(
                evidence,
                succeeded=counts[LoadRequestOutcome.SUCCEEDED],
                rejected=counts[LoadRequestOutcome.REJECTED],
                timed_out=counts[LoadRequestOutcome.TIMED_OUT],
                failed=counts[LoadRequestOutcome.FAILED],
            )

    def _evict_terminal_runs(self) -> None:
        while len(self._runs) >= self._policy.max_retained_runs:
            terminal = next(
                (run_id for run_id, run in self._runs.items() if run.status is not LoadRunStatus.RUNNING),
                None,
            )
            if terminal is None:
                return
            del self._runs[terminal]


def http_load_generator_block(
    block_id: str = "http-load-generator",
    *,
    display_name: str = "HTTP Load Generator",
    image: str = "control-plane-kit:local",
    policy: LoadGeneratorPolicy,
    control_secret_reference: str = "secret://http-load-generator/control-token",
) -> ApplicationBlock:
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_LOAD_GENERATOR,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.LOAD_STATE_READABLE,
                CapabilityName.LOAD_MUTABLE,
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=http_load_generator_command(policy),
            ports={"control": 8080},
            environment=(PublicStaticEnvironmentBinding("CPK_TEST_ONLY", "1"),),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_LOAD_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("LOAD_TARGET_URL",)),),
            providers=(ProviderSocket("control", Protocol.HTTP),),
        ),
    )


def http_load_generator_command(
    policy: LoadGeneratorPolicy,
) -> tuple[str, ...]:
    """Return the process command for one typed load-generator policy."""

    if not isinstance(policy, LoadGeneratorPolicy):
        raise TypeError("load generator command requires a typed policy")
    policy_json = json.dumps(
        policy.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "python",
        "-m",
        "control_plane_kit.load_generator_server.main",
        policy_json,
    )


def create_load_generator_app(
    server: HttpLoadGeneratorServer,
    *,
    control_token: str,
    test_only: bool,
) -> FastAPI:
    if not control_token:
        raise ValueError("load generator control token is required")
    if not test_only:
        raise ValueError("load generator refuses production-mode admission")
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        server.shutdown()

    app = FastAPI(title="control-plane-kit HTTP load generator", lifespan=lifespan)

    def authorized(request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {control_token}")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "maturity": "test-only"}

    @app.post("/__deploy/load-runs")
    async def trigger(request: Request) -> Response:
        if not authorized(request):
            return Response(status_code=401, content=b"Unauthorized")
        try:
            descriptor = json.loads((await _bounded_body(request)).decode())
            command = load_run_command_from_descriptor(descriptor)
            record, replayed = server.trigger(command)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return JSONResponse(status_code=422, content={"error": str(exc)})
        except LoadRunConflict as exc:
            return JSONResponse(status_code=409, content={"error": str(exc)})
        except LoadGeneratorCapacityExhausted as exc:
            return JSONResponse(status_code=429, content={"error": str(exc)})
        return JSONResponse(status_code=200 if replayed else 202, content=record.descriptor())

    @app.get("/__deploy/load-runs/{run_id}")
    def read(run_id: str, request: Request) -> Response:
        if not authorized(request):
            return Response(status_code=401, content=b"Unauthorized")
        try:
            return JSONResponse(content=server.read(run_id).descriptor())
        except KeyError:
            return Response(status_code=404, content=b"Not Found")

    @app.post("/__deploy/load-runs/{run_id}/cancel")
    def cancel(run_id: str, request: Request) -> Response:
        if not authorized(request):
            return Response(status_code=401, content=b"Unauthorized")
        try:
            return JSONResponse(status_code=202, content=server.cancel(run_id).descriptor())
        except KeyError:
            return Response(status_code=404, content=b"Not Found")

    return app


async def _bounded_body(request: Request, maximum: int = 8_192) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise ValueError("load-run command body exceeds limit")
        chunks.append(chunk)
    return b"".join(chunks)
