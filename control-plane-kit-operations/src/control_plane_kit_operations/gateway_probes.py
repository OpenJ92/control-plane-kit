"""Authorized, durable delegation of bounded runtime-island gateway probes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Callable, Mapping, Protocol

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import RuntimeEndpointObservation
from control_plane_kit_core.runtime_effects import (
    GatewayHttpTarget,
    GatewayPostgresTarget,
)
from control_plane_kit_core.secrets import (
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.records import BoundedEvidence
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.runtime_effects import (
    gateway_control_endpoint_for_node,
    gateway_target_map_for_node,
    named_public_gateway_endpoint_for_node,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderRegistrationError,
    SecretUseResolutionAuthorizer,
    secret_use_correlation_for,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class GatewayProbeError(RuntimeError):
    """Base error for rejected or incoherent gateway probe commands."""


class GatewayProbeAuthorizationDenied(GatewayProbeError):
    """Raised when trusted authority does not permit gateway probing."""


class GatewayProbeNotFound(GatewayProbeError):
    """Raised when current graph truth cannot resolve the requested target."""


class GatewayProbeConflict(GatewayProbeError):
    """Raised for stale graph or conflicting idempotent intent."""


class GatewayProbeDispatchError(GatewayProbeError):
    """Bounded failure raised by an injected gateway dispatcher."""


class GatewayProbeAttemptStatus(StrEnum):
    """Durable states surrounding one external gateway probe effect."""

    INTENDED = "intended"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class GatewayProbeDispatchResult:
    """Bounded, secret-free result returned by the external dispatcher."""

    status: GatewayProbeAttemptStatus
    code: str
    evidence: BoundedEvidence = BoundedEvidence()

    def __post_init__(self) -> None:
        if self.status not in {
            GatewayProbeAttemptStatus.SUCCEEDED,
            GatewayProbeAttemptStatus.REJECTED,
            GatewayProbeAttemptStatus.FAILED,
        }:
            raise GatewayProbeError("dispatch result must be terminal")
        _required_text(self.code, "dispatch result code")
        if not isinstance(self.evidence, BoundedEvidence):
            raise GatewayProbeError("dispatch evidence must be bounded")


@dataclass(frozen=True)
class GatewayProbeDispatch:
    """Transient effect material passed to an injected signer/transport."""

    grant: DelegatedGatewayProbeGrant
    request: GatewayProbeRequest
    gateway_endpoint: RuntimeEndpointObservation
    signing_key_reference: SecretReference
    signing_public_key: DelegationPublicKey
    secret_resolution_grant: SecretResolutionGrant

    def __post_init__(self) -> None:
        if not isinstance(self.grant, DelegatedGatewayProbeGrant):
            raise GatewayProbeError("dispatch grant is malformed")
        if not isinstance(self.request, GatewayProbeRequest):
            raise GatewayProbeError("dispatch request is malformed")
        if not isinstance(self.gateway_endpoint, RuntimeEndpointObservation):
            raise GatewayProbeError(
                "gateway endpoint must be a typed runtime observation"
            )
        if not isinstance(self.signing_key_reference, SecretReference):
            raise GatewayProbeError("gateway signing key reference is malformed")
        if not isinstance(self.signing_public_key, DelegationPublicKey):
            raise GatewayProbeError("gateway signing public key is malformed")
        if self.grant.key_id != self.signing_public_key.key_id:
            raise GatewayProbeError("gateway signing key identity is inconsistent")
        if not isinstance(self.secret_resolution_grant, SecretResolutionGrant):
            raise GatewayProbeError(
                "gateway signing requires a secret resolution grant"
            )


class GatewayProbeDispatcher(Protocol):
    """Sign and dispatch one exact probe outside operations transactions."""

    def dispatch(self, request: GatewayProbeDispatch) -> GatewayProbeDispatchResult: ...


@dataclass(frozen=True)
class GatewayProbeVerifierConfiguration:
    """Bounded public verifier material for one explicit gateway node."""

    issuer: str
    audience: str
    gateway_node_id: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self) -> None:
        _required_text(self.issuer, "issuer")
        _required_text(self.audience, "audience")
        _required_text(self.gateway_node_id, "gateway_node_id")
        keys = tuple(sorted(self.public_keys, key=lambda value: value.key_id))
        if not keys or len(keys) > 16 or not all(
            isinstance(value, DelegationPublicKey) for value in keys
        ):
            raise GatewayProbeError("gateway verifier key set is unavailable")
        if len({value.key_id for value in keys}) != len(keys):
            raise GatewayProbeError("gateway verifier key ids must be unique")
        object.__setattr__(self, "public_keys", keys)

    def public_environment(self) -> tuple[PublicStaticEnvironmentBinding, ...]:
        key_map = {key.key_id: key.public_key_pem for key in self.public_keys}
        return tuple(
            sorted(
                (
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_AUDIENCE",
                        self.audience,
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_ISSUER",
                        self.issuer,
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_NODE_ID",
                        self.gateway_node_id,
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON",
                        json.dumps(
                            key_map,
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_VERIFIER",
                        "ed25519",
                    ),
                )
            )
        )


class GatewayProbeVerifierConfigurationService:
    """Read public overlap material from durable key lifecycle truth."""

    def __init__(self, unit_of_work_factory: Callable[[], Any]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def for_gateway(
        self,
        *,
        workspace_id: str,
        gateway_node_id: str,
    ) -> GatewayProbeVerifierConfiguration:
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                stores.workspaces.get(workspace_id)
                active = stores.delegation_signing_keys.require_unambiguous_active(
                    workspace_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                )
                verifier_keys = stores.delegation_signing_keys.list_for_verification(
                    workspace_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    active.issuer,
                )
            except (KeyError, DelegationSigningKeyNotFound) as error:
                raise GatewayProbeNotFound(
                    "gateway verifier configuration is unavailable"
                ) from error
            unit_of_work.commit()
        if not any(
            value.status is RegisteredDelegationSigningKeyStatus.ACTIVE
            for value in verifier_keys
        ):
            raise GatewayProbeConflict("gateway verifier set has no active key")
        return GatewayProbeVerifierConfiguration(
            issuer=active.issuer,
            audience=f"gateway:{workspace_id}:{gateway_node_id}",
            gateway_node_id=gateway_node_id,
            public_keys=tuple(value.public_key for value in verifier_keys),
        )


@dataclass(frozen=True)
class GatewayProbeAttempt:
    """Durable authorization, intent, correlation, and bounded result evidence."""

    probe_id: str
    workspace_id: str
    request_id: str
    actor_id: str
    current_graph_id: str
    gateway_node_id: str
    gateway_runtime_id: str
    access_path: GatewayProbeAccessPath
    probe_kind: GatewayProbeCommandKind
    target_id: str
    request_digest: str
    issuer: str
    key_id: str
    audience: str
    grant_jti: str
    issued_at: int
    expires_at: int
    status: GatewayProbeAttemptStatus
    requested_at: str
    intent_fingerprint: str
    completed_at: str | None = None
    result_code: str | None = None
    evidence: BoundedEvidence = BoundedEvidence()

    def __post_init__(self) -> None:
        for value, name in (
            (self.probe_id, "probe_id"),
            (self.workspace_id, "workspace_id"),
            (self.request_id, "request_id"),
            (self.actor_id, "actor_id"),
            (self.current_graph_id, "current_graph_id"),
            (self.gateway_node_id, "gateway_node_id"),
            (self.gateway_runtime_id, "gateway_runtime_id"),
            (self.target_id, "target_id"),
            (self.request_digest, "request_digest"),
            (self.issuer, "issuer"),
            (self.key_id, "key_id"),
            (self.audience, "audience"),
            (self.grant_jti, "grant_jti"),
            (self.requested_at, "requested_at"),
            (self.intent_fingerprint, "intent_fingerprint"),
        ):
            _required_text(value, name)
        if not isinstance(self.probe_kind, GatewayProbeCommandKind):
            raise GatewayProbeError("probe_kind must be closed")
        if not isinstance(self.access_path, GatewayProbeAccessPath):
            raise GatewayProbeError("access_path must be closed")
        if not isinstance(self.status, GatewayProbeAttemptStatus):
            raise GatewayProbeError("attempt status must be closed")
        if type(self.issued_at) is not int or type(self.expires_at) is not int:
            raise GatewayProbeError("grant times must be integer epoch seconds")
        if self.expires_at <= self.issued_at:
            raise GatewayProbeError("grant expiry must follow issue time")
        terminal = self.status is not GatewayProbeAttemptStatus.INTENDED
        if terminal != (self.completed_at is not None and self.result_code is not None):
            raise GatewayProbeError("terminal attempt requires completion evidence")
        if not isinstance(self.evidence, BoundedEvidence):
            raise GatewayProbeError("attempt evidence must be bounded")

    def descriptor(self) -> dict[str, object]:
        return {
            "probe_id": self.probe_id,
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "actor_id": self.actor_id,
            "current_graph_id": self.current_graph_id,
            "gateway_node_id": self.gateway_node_id,
            "gateway_runtime_id": self.gateway_runtime_id,
            "access_path": self.access_path.value,
            "probe_kind": self.probe_kind.value,
            "target_id": self.target_id,
            "request_digest": self.request_digest,
            "grant": {
                "issuer": self.issuer,
                "key_id": self.key_id,
                "audience": self.audience,
                "jti": self.grant_jti,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
            },
            "status": self.status.value,
            "requested_at": self.requested_at,
            "completed_at": self.completed_at,
            "result_code": self.result_code,
            "evidence": self.evidence.descriptor(),
        }


@dataclass(frozen=True)
class RequestGatewayProbe:
    """Trusted request for one graph-declared probe through one gateway."""

    context: TrustedCommandContext
    request_id: str
    expected_current_graph_id: str
    gateway_node_id: str
    request: GatewayProbeRequest
    access_path: GatewayProbeAccessPath = GatewayProbeAccessPath.RUNTIME_PRIVATE

    def __post_init__(self) -> None:
        if not isinstance(self.context, TrustedCommandContext):
            raise GatewayProbeError("trusted command context is required")
        _required_text(self.request_id, "request_id")
        _required_text(self.expected_current_graph_id, "expected_current_graph_id")
        _required_text(self.gateway_node_id, "gateway_node_id")
        if not isinstance(self.access_path, GatewayProbeAccessPath):
            raise GatewayProbeError("gateway probe access_path must be closed")
        if not isinstance(self.request, GatewayProbeRequest):
            raise GatewayProbeError("gateway probe request is malformed")


@dataclass(frozen=True)
class GatewayProbeCommandResult:
    """One durable attempt returned by the command boundary."""

    attempt: GatewayProbeAttempt
    replayed: bool = False

    def descriptor(self) -> dict[str, object]:
        return {
            "gateway_probe": self.attempt.descriptor(),
            "replayed": self.replayed,
        }


class GatewayProbeCommandService:
    """Authorize, persist, dispatch, and fold one bounded gateway probe."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        dispatcher: GatewayProbeDispatcher,
        secret_use_authorizer: SecretUseResolutionAuthorizer,
        epoch_clock: Callable[[], int],
        clock: Callable[[], str],
        id_factory: Callable[[], str],
        grant_lifetime_seconds: int = 60,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._dispatcher = dispatcher
        if not hasattr(secret_use_authorizer, "authorize_resolution"):
            raise GatewayProbeError(
                "gateway secret use authorizer must expose authorize_resolution"
            )
        self._secret_use_authorizer = secret_use_authorizer
        self._epoch_clock = epoch_clock
        self._clock = clock
        self._id_factory = id_factory
        if not 1 <= grant_lifetime_seconds <= 300:
            raise GatewayProbeError("grant lifetime must be from 1 through 300 seconds")
        self._grant_lifetime_seconds = grant_lifetime_seconds

    def execute(self, command: RequestGatewayProbe) -> GatewayProbeCommandResult:
        if not isinstance(command, RequestGatewayProbe):
            raise GatewayProbeError("execute requires RequestGatewayProbe")
        if PolicyScope.GATEWAY_PROBE_USE not in command.context.granted_scopes:
            raise GatewayProbeAuthorizationDenied("scope gateway-probe:use is missing")
        if PolicyScope.DELEGATION_KEY_USE not in command.context.granted_scopes:
            raise GatewayProbeAuthorizationDenied("scope delegation-key:use is missing")
        fingerprint = _intent_fingerprint(command)

        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            stores.gateway_probes.lock_request_id(
                command.context.workspace_id,
                command.request_id,
            )
            existing = stores.gateway_probes.get_by_request_id(
                command.context.workspace_id,
                command.request_id,
            )
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise GatewayProbeConflict(
                        "gateway probe request_id was reused for different intent"
                    )
                unit_of_work.commit()
                return GatewayProbeCommandResult(existing, replayed=True)

            try:
                workspace = stores.workspaces.get_for_update(
                    command.context.workspace_id
                )
            except KeyError as error:
                raise GatewayProbeNotFound("workspace was not found") from error
            if workspace.current_graph_id != command.expected_current_graph_id:
                raise GatewayProbeConflict("current graph pointer is stale")
            try:
                graph_record = stores.graphs.get(command.expected_current_graph_id)
                graph = DEFAULT_GRAPH_CODEC.decode(graph_record.graph_descriptor)
                gateway_node = graph.nodes[command.gateway_node_id]
            except (KeyError, ValueError) as error:
                raise GatewayProbeNotFound(
                    "current graph gateway was not found"
                ) from error
            if graph_record.workspace_id != command.context.workspace_id:
                raise GatewayProbeConflict("current graph belongs to another workspace")
            products = stores.registered_products.list_active(
                command.context.workspace_id
            )
            target_map = gateway_target_map_for_node(
                graph,
                node_id=command.gateway_node_id,
                registered_products=products,
            )
            target = next(
                (
                    value
                    for value in target_map.targets
                    if value.target_id == command.request.target_id
                ),
                None,
            )
            if target is None:
                raise GatewayProbeNotFound(
                    "gateway target is not declared by the current graph"
                )
            _require_matching_probe_kind(command.request.kind, target)
            try:
                if command.access_path is GatewayProbeAccessPath.RUNTIME_PRIVATE:
                    endpoint = gateway_control_endpoint_for_node(
                        graph,
                        graph_id=graph_record.graph_id,
                        node_id=command.gateway_node_id,
                        registered_products=products,
                    )
                else:
                    endpoint = named_public_gateway_endpoint_for_node(
                        graph,
                        graph_id=graph_record.graph_id,
                        node_id=command.gateway_node_id,
                    )
            except InvalidOperationCommand as error:
                raise GatewayProbeConflict(str(error)) from error
            try:
                signing_key = stores.delegation_signing_keys.require_unambiguous_active(
                    command.context.workspace_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                )
            except DelegationSigningKeyNotFound as error:
                raise GatewayProbeConflict(
                    "active gateway delegation signing key is unavailable"
                ) from error
            issued_at = self._epoch_clock()
            probe_id = self._id_factory()
            grant_jti = self._id_factory()
            audience = (
                f"gateway:{command.context.workspace_id}:{command.gateway_node_id}"
            )
            grant = DelegatedGatewayProbeGrant(
                issuer=signing_key.issuer,
                key_id=signing_key.key_id,
                audience=audience,
                workspace_id=command.context.workspace_id,
                operation_id=probe_id,
                request_id=command.request_id,
                gateway_node_id=command.gateway_node_id,
                probe_kind=command.request.kind,
                target_id=command.request.target_id,
                request_digest=command.request.canonical_digest(),
                issued_at=issued_at,
                expires_at=issued_at + self._grant_lifetime_seconds,
                jti=grant_jti,
            )
            attempt = GatewayProbeAttempt(
                probe_id=probe_id,
                workspace_id=command.context.workspace_id,
                request_id=command.request_id,
                actor_id=command.context.actor_id,
                current_graph_id=graph_record.graph_id,
                gateway_node_id=command.gateway_node_id,
                gateway_runtime_id=gateway_node.runtime_id,
                access_path=command.access_path,
                probe_kind=command.request.kind,
                target_id=command.request.target_id.value,
                request_digest=grant.request_digest.value,
                issuer=grant.issuer,
                key_id=grant.key_id,
                audience=grant.audience,
                grant_jti=grant.jti,
                issued_at=grant.issued_at,
                expires_at=grant.expires_at,
                status=GatewayProbeAttemptStatus.INTENDED,
                requested_at=self._clock(),
                intent_fingerprint=fingerprint,
            )
            stores.gateway_probes.add(attempt)
            unit_of_work.commit()

        try:
            resolution_grant = self._authorize_signing_key(
                command,
                attempt,
                signing_key,
            )
        except SecretProviderRegistrationError:
            dispatch_result = GatewayProbeDispatchResult(
                status=GatewayProbeAttemptStatus.REJECTED,
                code="gateway-signing-secret-not-authorized",
            )
        except GatewayProbeError:
            dispatch_result = GatewayProbeDispatchResult(
                status=GatewayProbeAttemptStatus.REJECTED,
                code="gateway-signing-secret-grant-invalid",
            )
        else:
            try:
                dispatch_result = self._dispatcher.dispatch(
                    GatewayProbeDispatch(
                        grant,
                        command.request,
                        endpoint,
                        signing_key.private_key_reference,
                        signing_key.public_key,
                        resolution_grant,
                    )
                )
            except GatewayProbeDispatchError as error:
                dispatch_result = GatewayProbeDispatchResult(
                    status=GatewayProbeAttemptStatus.FAILED,
                    code="gateway-dispatch-failed",
                    evidence=BoundedEvidence.from_mapping(
                        {"error_type": type(error).__name__}
                    ),
                )

        with self._unit_of_work_factory() as unit_of_work:
            completed = unit_of_work.stores.gateway_probes.complete(
                attempt.probe_id,
                status=dispatch_result.status,
                completed_at=self._clock(),
                result_code=dispatch_result.code,
                evidence=dispatch_result.evidence,
            )
            unit_of_work.commit()
        return GatewayProbeCommandResult(completed)

    def _authorize_signing_key(
        self,
        command: RequestGatewayProbe,
        attempt: GatewayProbeAttempt,
        signing_key: RegisteredDelegationSigningKey,
    ) -> SecretResolutionGrant:
        intent = SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY
        resolution_grant = self._secret_use_authorizer.authorize_resolution(
            AuthorizeSecretUse(
                workspace_id=command.context.workspace_id,
                reference=signing_key.private_key_reference,
                intent=intent,
                actor_subject=command.context.actor_id,
                correlation_id=secret_use_correlation_for(
                    workspace_id=command.context.workspace_id,
                    reference=signing_key.private_key_reference,
                    intent=intent,
                    actor_subject=command.context.actor_id,
                    operation_id=attempt.probe_id,
                    probe_id=attempt.probe_id,
                ),
                requested_at=attempt.requested_at,
                actor_scopes=command.context.granted_scopes,
                operation_id=attempt.probe_id,
                probe_id=attempt.probe_id,
            )
        )
        if (
            not isinstance(resolution_grant, SecretResolutionGrant)
            or resolution_grant.workspace_id != command.context.workspace_id
            or resolution_grant.probe_id != attempt.probe_id
            or not resolution_grant.permits(
                signing_key.private_key_reference,
                intent,
            )
        ):
            raise GatewayProbeError(
                "secret use authorizer returned an invalid gateway grant"
            )
        return resolution_grant


def _require_matching_probe_kind(kind: GatewayProbeCommandKind, target: object) -> None:
    if kind is GatewayProbeCommandKind.HTTP_STATUS and isinstance(
        target, GatewayHttpTarget
    ):
        return
    if kind is GatewayProbeCommandKind.POSTGRES_SELECT_ONE and isinstance(
        target, GatewayPostgresTarget
    ):
        return
    raise GatewayProbeConflict("gateway probe kind does not match declared target")


def _intent_fingerprint(command: RequestGatewayProbe) -> str:
    payload: Mapping[str, object] = {
        "workspace_id": command.context.workspace_id,
        "expected_current_graph_id": command.expected_current_graph_id,
        "gateway_node_id": command.gateway_node_id,
        "access_path": command.access_path.value,
        "request": command.request.descriptor(),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GatewayProbeError(f"{field} must be nonempty text")
