"""Interpret graph-pinned health effects as bounded truthful probes."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from control_plane_kit.adapters.probes.clients import (
    ApplicationHealthProbeAdapter,
    ProcessProbeAdapter,
    RuntimeEndpointProvider,
    TransportProbeAdapter,
)
from control_plane_kit.adapters.probes.security import ProbeSecurityError
from control_plane_kit.effects.material import MaterializedEffectRequest, NodeMaterial
from control_plane_kit.effects.probes import (
    ApplicationHealthProbeIntent,
    ProbeConstructionFailure,
    ProbeKind,
    ProbeObservation,
    ProbeOutcome,
    ProbePolicy,
    ProcessProbeIntent,
    ReadinessProbeIntent,
    TransportProbeIntent,
    application_health_probe,
    process_probe,
    transport_probe,
)
from control_plane_kit.effects.values import (
    EffectCapability,
    EffectFailed,
    EffectObservation,
    EffectResult,
    EffectSucceeded,
    EffectUnsupported,
    ObservationKind,
)
from control_plane_kit.execution import (
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
    ObservationStatus,
)
from control_plane_kit.planning import WaitForHealthy


MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class ProbeEffectInterpreter:
    """Run process, transport, and health probes without persistence access."""

    endpoints: RuntimeEndpointProvider
    transport: TransportProbeAdapter
    application: ApplicationHealthProbeAdapter
    process: ProcessProbeAdapter | None = None
    monotonic: MonotonicClock = time.monotonic
    sleep: Sleeper = time.sleep

    @property
    def capabilities(self) -> frozenset[EffectCapability]:
        return frozenset({EffectCapability.HEALTH_PROBE})

    def execute(self, request: MaterializedEffectRequest) -> EffectResult:
        if not isinstance(request.action, WaitForHealthy):
            return EffectUnsupported(request.identity, request.capability)
        if not isinstance(request.material, NodeMaterial):
            return self._terminal_failure(
                request,
                "probe.invalid-material",
                "Health probing requires graph-pinned node material.",
            )

        try:
            endpoint = self.endpoints.endpoint_for(
                request.material.node_id,
                request.material_graph_id,
            )
            policy = _policy_for(request)
            process_intent = process_probe(
                request.material,
                request.material_graph_id,
                policy,
            )
            transport_intent = transport_probe(request.material, endpoint, policy)
            health_intent = application_health_probe(request.material, endpoint, policy)
            if isinstance(transport_intent, ProbeConstructionFailure):
                return self._construction_failure(request, transport_intent)
            if isinstance(health_intent, ProbeConstructionFailure):
                return self._construction_failure(request, health_intent)
        except ProbeSecurityError:
            return self._terminal_failure(
                request,
                "probe.address-rejected",
                "The runtime endpoint was rejected by probe address policy.",
            )
        except (KeyError, TypeError, ValueError):
            return self._terminal_failure(
                request,
                "probe.endpoint-unavailable",
                "No valid graph-correlated runtime endpoint is available.",
            )

        deadline = self.monotonic() + request.timeout.total_seconds
        last: tuple[ProbeObservation, ...] = ()
        for attempt in range(1, policy.maximum_attempts + 1):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                last = _timeout_observations(
                    request.material.node_id,
                    request.material_graph_id,
                    endpoint.context,
                    attempt,
                )
                break
            try:
                last = self._attempt(
                    request,
                    process_intent,
                    transport_intent,
                    health_intent,
                    attempt=attempt,
                    deadline=deadline,
                )
            except ProbeSecurityError:
                return self._terminal_failure(
                    request,
                    "probe.address-rejected",
                    "The runtime endpoint was rejected by probe address policy.",
                )
            if last[-1].outcome is ProbeOutcome.READY:
                return EffectSucceeded(
                    request.identity,
                    BoundedEvidence.from_mapping(
                        {
                            "operation": "wait-for-healthy",
                            "attempts": attempt,
                            "graph_id": request.material_graph_id,
                        }
                    ),
                    tuple(_effect_observation(value) for value in last),
                )
            if _terminal_nonreadiness(last):
                break
            if attempt >= policy.maximum_attempts:
                break
            interval = request.timeout.interval_seconds
            if interval is None:
                break
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                break
            self.sleep(min(float(interval), remaining))

        return _failed_probe_result(request, last)

    def _attempt(
        self,
        request: MaterializedEffectRequest,
        process_intent: ProcessProbeIntent,
        transport_intent: TransportProbeIntent,
        health_intent: ApplicationHealthProbeIntent,
        *,
        attempt: int,
        deadline: float,
    ) -> tuple[ProbeObservation, ...]:
        observed: list[ProbeObservation] = []
        required: list[ProbeKind] = []
        if self.process is not None:
            remaining = deadline - self.monotonic()
            if remaining < 1:
                return _timeout_observations(
                    process_intent.subject_id,
                    process_intent.graph_id,
                    transport_intent.endpoint.context,
                    attempt,
                )
            process = self.process.observe(
                process_intent,
                request,
                timeout_seconds=remaining,
            )
            if process is not None:
                process = _at_attempt(process, attempt)
                observed.append(process)
                required.append(ProbeKind.PROCESS)
                if process.outcome is not ProbeOutcome.PROCESS_RUNNING:
                    return _not_ready(observed, process_intent, required, attempt)

        remaining = deadline - self.monotonic()
        if remaining <= 0:
            return _timeout_observations(
                process_intent.subject_id,
                process_intent.graph_id,
                transport_intent.endpoint.context,
                attempt,
            )
        transport = _at_attempt(
            self.transport.observe(
                transport_intent,
                timeout_seconds=remaining,
            ),
            attempt,
        )
        observed.append(transport)
        required.append(ProbeKind.TRANSPORT)
        if transport.outcome is not ProbeOutcome.REACHABLE:
            return _not_ready(observed, process_intent, required, attempt)

        remaining = deadline - self.monotonic()
        if remaining <= 0:
            return _timeout_observations(
                process_intent.subject_id,
                process_intent.graph_id,
                transport_intent.endpoint.context,
                attempt,
            )
        health = _at_attempt(
            self.application.observe(
                health_intent,
                timeout_seconds=remaining,
            ),
            attempt,
        )
        observed.append(health)
        required.append(ProbeKind.APPLICATION_HEALTH)
        readiness_intent = ReadinessProbeIntent(
            process_intent.subject_id,
            process_intent.graph_id,
            tuple(required),
        )
        observed.append(
            ProbeObservation(
                readiness_intent.subject_id,
                readiness_intent.graph_id,
                readiness_intent.kind,
                (
                    ProbeOutcome.READY
                    if health.outcome is ProbeOutcome.HEALTHY
                    else ProbeOutcome.NOT_READY
                ),
                attempts=attempt,
            )
        )
        return tuple(observed)

    @staticmethod
    def _construction_failure(
        request: MaterializedEffectRequest,
        failure: ProbeConstructionFailure,
    ) -> EffectFailed:
        return ProbeEffectInterpreter._terminal_failure(
            request,
            f"probe.{failure.code.value}",
            "The graph-pinned probe contract cannot be interpreted.",
        )

    @staticmethod
    def _terminal_failure(
        request: MaterializedEffectRequest,
        code: str,
        message: str,
    ) -> EffectFailed:
        return EffectFailed(
            request.identity,
            FailureEvidence(FailureCategory.TERMINAL, code, message),
        )


def _policy_for(request: MaterializedEffectRequest) -> ProbePolicy:
    interval = request.timeout.interval_seconds
    attempts = (
        1
        if interval is None
        else min(100, 1 + request.timeout.total_seconds // interval)
    )
    return ProbePolicy(request.timeout, maximum_attempts=attempts)


def _at_attempt(value: ProbeObservation, attempt: int) -> ProbeObservation:
    return ProbeObservation(
        value.subject_id,
        value.graph_id,
        value.kind,
        value.outcome,
        attempts=attempt,
        endpoint_context=value.endpoint_context,
    )


def _not_ready(
    observed: list[ProbeObservation],
    process_intent: ProcessProbeIntent,
    required: list[ProbeKind],
    attempt: int,
) -> tuple[ProbeObservation, ...]:
    readiness = ReadinessProbeIntent(
        process_intent.subject_id,
        process_intent.graph_id,
        tuple(required),
    )
    return (
        *observed,
        ProbeObservation(
            readiness.subject_id,
            readiness.graph_id,
            readiness.kind,
            ProbeOutcome.NOT_READY,
            attempts=attempt,
        ),
    )


def _timeout_observations(
    subject_id: str,
    graph_id: str,
    context,
    attempt: int,
) -> tuple[ProbeObservation, ...]:
    return (
        ProbeObservation(
            subject_id,
            graph_id,
            ProbeKind.APPLICATION_HEALTH,
            ProbeOutcome.TIMED_OUT,
            attempts=attempt,
            endpoint_context=context,
        ),
        ProbeObservation(
            subject_id,
            graph_id,
            ProbeKind.READINESS,
            ProbeOutcome.NOT_READY,
            attempts=attempt,
        ),
    )


def _effect_observation(value: ProbeObservation) -> EffectObservation:
    status = {
        ProbeOutcome.PROCESS_RUNNING: ObservationStatus.PROCESS_STARTED,
        ProbeOutcome.PROCESS_STOPPED: ObservationStatus.UNKNOWN,
        ProbeOutcome.REACHABLE: ObservationStatus.REACHABLE,
        ProbeOutcome.REFUSED: ObservationStatus.UNHEALTHY,
        ProbeOutcome.HEALTHY: ObservationStatus.HEALTHY,
        ProbeOutcome.UNHEALTHY: ObservationStatus.UNHEALTHY,
        ProbeOutcome.TIMED_OUT: ObservationStatus.TIMED_OUT,
        ProbeOutcome.MALFORMED: ObservationStatus.UNKNOWN,
        ProbeOutcome.UNKNOWN: ObservationStatus.UNKNOWN,
        ProbeOutcome.READY: ObservationStatus.HEALTHY,
        ProbeOutcome.NOT_READY: ObservationStatus.UNHEALTHY,
    }[value.outcome]
    kind = (
        ObservationKind.STATUS
        if value.kind is ProbeKind.PROCESS
        else ObservationKind.HEALTH
    )
    return EffectObservation(
        value.subject_id,
        kind,
        status,
        BoundedEvidence.from_mapping(value.descriptor()),
        graph_id=value.graph_id,
        probe_kind=value.kind,
        probe_outcome=value.outcome,
        endpoint_context=value.endpoint_context,
    )


def _failed_probe_result(
    request: MaterializedEffectRequest,
    observations: tuple[ProbeObservation, ...],
) -> EffectFailed:
    concrete = next(
        (
            value
            for value in reversed(observations)
            if value.kind is not ProbeKind.READINESS
        ),
        None,
    )
    outcome = ProbeOutcome.UNKNOWN if concrete is None else concrete.outcome
    category, code, message = {
        ProbeOutcome.PROCESS_STOPPED: (
            FailureCategory.RETRYABLE,
            "probe.process-stopped",
            "The runtime process is not running.",
        ),
        ProbeOutcome.REFUSED: (
            FailureCategory.RETRYABLE,
            "probe.connection-refused",
            "The runtime endpoint refused the probe connection.",
        ),
        ProbeOutcome.TIMED_OUT: (
            FailureCategory.RETRYABLE,
            "probe.timed-out",
            "The bounded probe timed out before readiness.",
        ),
        ProbeOutcome.UNHEALTHY: (
            FailureCategory.TERMINAL,
            "probe.application-unhealthy",
            "The application reported an unhealthy response.",
        ),
        ProbeOutcome.MALFORMED: (
            FailureCategory.TERMINAL,
            "probe.application-malformed",
            "The application health response was malformed.",
        ),
        ProbeOutcome.UNKNOWN: (
            FailureCategory.OPERATOR_REVIEW,
            "probe.unknown",
            "The runtime probe could not establish truthful readiness.",
        ),
    }.get(
        outcome,
        (
            FailureCategory.RETRYABLE,
            "probe.not-ready",
            "The application did not become ready within the bounded probe.",
        ),
    )
    attempts = max((value.attempts for value in observations), default=1)
    return EffectFailed(
        request.identity,
        FailureEvidence(
            category,
            code,
            message,
            BoundedEvidence.from_mapping(
                {"outcome": outcome.value, "attempts": attempts}
            ),
        ),
        tuple(_effect_observation(value) for value in observations),
    )


def _terminal_nonreadiness(
    observations: tuple[ProbeObservation, ...],
) -> bool:
    return any(
        value.outcome in (ProbeOutcome.UNHEALTHY, ProbeOutcome.MALFORMED)
        for value in observations
    )
