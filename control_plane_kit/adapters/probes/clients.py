"""Concrete process, transport, and application-health probe adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
import socket
from typing import Mapping, Protocol

import httpx

from control_plane_kit.adapters.probes.security import (
    ProbeAddressPolicy,
    ProbeEndpointSecretResolver,
    ProbePublicAddressResolver,
    ProbeSecurityError,
    authorize_probe_endpoint,
)
from control_plane_kit.effects.material import MaterializedEffectRequest, NodeMaterial
from control_plane_kit.effects.probes import (
    ApplicationHealthProbeIntent,
    ProbeObservation,
    ProbeOutcome,
    ProcessProbeIntent,
    RuntimeEndpointObservation,
    TransportProbeIntent,
)


class RuntimeEndpointProvider(Protocol):
    """Supply graph-correlated runtime endpoint evidence without exposing stores."""

    def endpoint_for(
        self,
        subject_id: str,
        graph_id: str,
    ) -> RuntimeEndpointObservation: ...


class ProcessProbeAdapter(Protocol):
    def observe(
        self,
        intent: ProcessProbeIntent,
        request: MaterializedEffectRequest,
        *,
        timeout_seconds: float,
    ) -> ProbeObservation | None: ...


class TransportProbeAdapter(Protocol):
    def observe(
        self,
        intent: TransportProbeIntent,
        *,
        timeout_seconds: float,
    ) -> ProbeObservation: ...


class ApplicationHealthProbeAdapter(Protocol):
    def observe(
        self,
        intent: ApplicationHealthProbeIntent,
        *,
        timeout_seconds: float,
    ) -> ProbeObservation: ...


@dataclass(frozen=True)
class StaticRuntimeEndpointProvider:
    """Small runtime registry for local deployments, tests, and examples."""

    endpoints: Mapping[tuple[str, str], RuntimeEndpointObservation]

    def endpoint_for(
        self,
        subject_id: str,
        graph_id: str,
    ) -> RuntimeEndpointObservation:
        try:
            endpoint = self.endpoints[(subject_id, graph_id)]
        except KeyError as error:
            raise KeyError("runtime endpoint observation is unavailable") from error
        if endpoint.subject_id != subject_id or endpoint.graph_id != graph_id:
            raise ValueError("runtime endpoint registry returned mismatched evidence")
        return endpoint


class SocketConnection(Protocol):
    def close(self) -> None: ...


class SocketConnector(Protocol):
    def connect(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> SocketConnection: ...


@dataclass(frozen=True)
class DefaultSocketConnector:
    def connect(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> SocketConnection:
        return socket.create_connection((host, port), timeout=timeout_seconds)


@dataclass(frozen=True)
class TcpTransportProbeAdapter:
    """Prove only TCP reachability; it makes no application-health claim."""

    policy: ProbeAddressPolicy
    connector: SocketConnector = field(default_factory=DefaultSocketConnector)
    secret_resolver: ProbeEndpointSecretResolver | None = None
    public_resolver: ProbePublicAddressResolver | None = None

    def observe(
        self,
        intent: TransportProbeIntent,
        *,
        timeout_seconds: float,
    ) -> ProbeObservation:
        target = authorize_probe_endpoint(
            intent.endpoint,
            self.policy,
            secret_resolver=self.secret_resolver,
            public_resolver=self.public_resolver,
        )
        outcome = ProbeOutcome.UNKNOWN
        connection: SocketConnection | None = None
        try:
            connection = self.connector.connect(
                target.connect_host,
                target.port,
                timeout_seconds=timeout_seconds,
            )
            outcome = ProbeOutcome.REACHABLE
        except (ConnectionRefusedError, ConnectionResetError):
            outcome = ProbeOutcome.REFUSED
        except (TimeoutError, socket.timeout):
            outcome = ProbeOutcome.TIMED_OUT
        except OSError:
            outcome = ProbeOutcome.UNKNOWN
        finally:
            if connection is not None:
                connection.close()
        return ProbeObservation(
            intent.subject_id,
            intent.graph_id,
            intent.kind,
            outcome,
            endpoint_context=intent.endpoint.context,
        )


@dataclass(frozen=True)
class HttpApplicationHealthProbeAdapter:
    """Perform one bounded redirect-free HTTP application-health request."""

    policy: ProbeAddressPolicy
    secret_resolver: ProbeEndpointSecretResolver | None = None
    public_resolver: ProbePublicAddressResolver | None = None
    transport: httpx.BaseTransport | None = None

    def observe(
        self,
        intent: ApplicationHealthProbeIntent,
        *,
        timeout_seconds: float,
    ) -> ProbeObservation:
        try:
            target = authorize_probe_endpoint(
                intent.endpoint,
                self.policy,
                secret_resolver=self.secret_resolver,
                public_resolver=self.public_resolver,
            )
            timeout = httpx.Timeout(
                timeout_seconds,
                connect=min(timeout_seconds, 5.0),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=min(timeout_seconds, 5.0),
            )
            headers = {"Accept": "application/json"}
            if target.host_header is not None:
                headers["Host"] = target.host_header
            with httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                request = client.build_request(
                    "GET",
                    target.request_url(intent.health_path),
                    headers=headers,
                )
                if target.sni_hostname is not None:
                    request.extensions["sni_hostname"] = target.sni_hostname
                response = client.send(request, stream=True)
                try:
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > intent.policy.maximum_response_bytes:
                            return self._observation(intent, ProbeOutcome.MALFORMED)
                finally:
                    response.close()
            if 300 <= response.status_code < 400:
                return self._observation(intent, ProbeOutcome.MALFORMED)
            outcome = (
                ProbeOutcome.HEALTHY
                if response.status_code in intent.policy.http.status_codes
                else ProbeOutcome.UNHEALTHY
            )
            return self._observation(intent, outcome)
        except ProbeSecurityError:
            raise
        except httpx.TimeoutException:
            return self._observation(intent, ProbeOutcome.TIMED_OUT)
        except httpx.ConnectError as error:
            outcome = (
                ProbeOutcome.REFUSED
                if isinstance(error.__cause__, ConnectionRefusedError)
                else ProbeOutcome.UNKNOWN
            )
            return self._observation(intent, outcome)
        except httpx.RemoteProtocolError:
            return self._observation(intent, ProbeOutcome.MALFORMED)
        except httpx.HTTPError:
            return self._observation(intent, ProbeOutcome.UNKNOWN)

    @staticmethod
    def _observation(
        intent: ApplicationHealthProbeIntent,
        outcome: ProbeOutcome,
    ) -> ProbeObservation:
        return ProbeObservation(
            intent.subject_id,
            intent.graph_id,
            intent.kind,
            outcome,
            endpoint_context=intent.endpoint.context,
        )
