"""Authorize exact node-control intent without crossing an effect boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.node_control import (
    DelegatedWorkloadNodeControlGrant,
    NodeControlCanonicalization,
    NodeControlCommandRequest,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    workload_node_control_audience,
)
from control_plane_kit_core.node_control_transit import (
    DelegatedGatewayNodeControlTransitGrant,
    DelegatedGatewayNodeControlTransitGrantProfile,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, Node
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyConflict,
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
)
from control_plane_kit_operations.node_control_attempts import (
    NodeControlAttemptConflict,
    NodeControlAttemptCorrupt,
    NodeControlAttemptError,
    NodeControlIntendedAttempt,
    node_control_intent_fingerprint,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    AuthorizedSecretUse,
    SecretProviderRegistrationError,
    authorize_secret_use_in_unit_of_work,
    secret_use_correlation_for,
)


class NodeControlIntentError(RuntimeError):
    """Base bounded error for node-control intent authorization."""


class NodeControlIntentAuthorizationDenied(NodeControlIntentError):
    """Raised when trusted operator authority lacks an exact command scope."""


class NodeControlIntentConflict(NodeControlIntentError):
    """Raised when replay, currentness, or authority truth conflicts."""


class NodeControlIntentNotFound(NodeControlIntentError):
    """Raised when accepted graph truth does not declare the requested target."""


_KEY_REGISTRATION = re.compile(r"dkey_[0-9a-f]{64}\Z")
_AUTHORIZATION = re.compile(r"suse_[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class RequestNodeControlIntent:
    """Trusted request to prepare one graph-bound command for later signing."""

    context: TrustedCommandContext
    gateway_node_id: NodeControlGraphReference
    request: NodeControlCommandRequest

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context, TrustedCommandContext)
            or not isinstance(self.gateway_node_id, NodeControlGraphReference)
            or self.gateway_node_id.role is not NodeControlGraphReferenceRole.NODE
            or not isinstance(self.request, NodeControlCommandRequest)
        ):
            raise NodeControlIntentError(
                "node-control intent request is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class DeferredGatewayNodeControlTransitSigningRequest:
    """Committed identities and exact unsigned transit payload for a later effect."""

    key_registration_id: str = field(repr=False)
    authorization_id: str = field(repr=False)
    grant: DelegatedGatewayNodeControlTransitGrant

    def __post_init__(self) -> None:
        if (
            type(self.key_registration_id) is not str
            or _KEY_REGISTRATION.fullmatch(self.key_registration_id) is None
            or type(self.authorization_id) is not str
            or _AUTHORIZATION.fullmatch(self.authorization_id) is None
            or not isinstance(self.grant, DelegatedGatewayNodeControlTransitGrant)
        ):
            raise NodeControlIntentError(
                "deferred transit signing request is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class DeferredWorkloadNodeControlSigningRequest:
    """Committed identities and exact unsigned workload payload for a later effect."""

    key_registration_id: str = field(repr=False)
    authorization_id: str = field(repr=False)
    grant: DelegatedWorkloadNodeControlGrant

    def __post_init__(self) -> None:
        if (
            type(self.key_registration_id) is not str
            or _KEY_REGISTRATION.fullmatch(self.key_registration_id) is None
            or type(self.authorization_id) is not str
            or _AUTHORIZATION.fullmatch(self.authorization_id) is None
            or not isinstance(self.grant, DelegatedWorkloadNodeControlGrant)
        ):
            raise NodeControlIntentError(
                "deferred workload signing request is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class NodeControlIntentPreparation:
    """One committed INTENDED attempt and its two post-commit signing requests."""

    attempt: NodeControlIntendedAttempt
    replayed: bool
    transit_signing: DeferredGatewayNodeControlTransitSigningRequest
    workload_signing: DeferredWorkloadNodeControlSigningRequest

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempt, NodeControlIntendedAttempt)
            or type(self.replayed) is not bool
            or not isinstance(
                self.transit_signing,
                DeferredGatewayNodeControlTransitSigningRequest,
            )
            or not isinstance(
                self.workload_signing,
                DeferredWorkloadNodeControlSigningRequest,
            )
            or self.transit_signing.key_registration_id
            != self.attempt.transit_key_registration_id
            or self.transit_signing.authorization_id
            != self.attempt.transit_authorization_id
            or self.transit_signing.grant != self.attempt.transit_grant
            or self.workload_signing.key_registration_id
            != self.attempt.workload_key_registration_id
            or self.workload_signing.authorization_id
            != self.attempt.workload_authorization_id
            or self.workload_signing.grant != self.attempt.workload_grant
        ):
            raise NodeControlIntentError(
                "node-control intent preparation is incoherent"
            ) from None


class NodeControlIntentAuthorizationService:
    """Commit exact graph, key, and secret-use authority before any effect."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        epoch_clock: Callable[[], int],
        clock: Callable[[], str],
        id_factory: Callable[[], str],
        grant_lifetime_seconds: int = 60,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._epoch_clock = epoch_clock
        self._clock = clock
        self._id_factory = id_factory
        if (
            type(grant_lifetime_seconds) is not int
            or not 1 <= grant_lifetime_seconds <= 300
        ):
            raise NodeControlIntentError(
                "node-control grant lifetime must be from 1 through 300 seconds"
            ) from None
        self._grant_lifetime_seconds = grant_lifetime_seconds

    def execute(
        self,
        command: RequestNodeControlIntent,
    ) -> NodeControlIntentPreparation:
        if not isinstance(command, RequestNodeControlIntent):
            raise NodeControlIntentError(
                "execute requires RequestNodeControlIntent"
            ) from None
        _require_scopes(command)
        try:
            fingerprint = node_control_intent_fingerprint(
                actor_subject=command.context.actor_id,
                gateway_node_id=command.gateway_node_id,
                request=command.request,
            )
        except NodeControlAttemptError:
            invalid_fingerprint = True
            fingerprint = None
        else:
            invalid_fingerprint = False
        if invalid_fingerprint or fingerprint is None:
            raise NodeControlIntentError(
                "node-control intent identity is malformed"
            ) from None

        prepared_attempt: NodeControlIntendedAttempt | None = None
        replayed = False
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            workspace_id = command.context.workspace_id
            request_id = command.request.request_id
            stores.node_control_attempts.lock_request_id(workspace_id, request_id)
            existing = _existing_attempt(stores, workspace_id, request_id)
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise NodeControlIntentConflict(
                        "node-control request identity conflicts with durable intent"
                    ) from None
                unit_of_work.commit()
                prepared_attempt = existing
                replayed = True
            else:
                attempt = self._prepare_new(unit_of_work, command)
                attempt_conflict = False
                attempt_invalid = False
                try:
                    stores.node_control_attempts.add(attempt)
                except NodeControlAttemptConflict:
                    attempt_conflict = True
                except (NodeControlAttemptError, NodeControlAttemptCorrupt):
                    attempt_invalid = True
                if attempt_conflict:
                    raise NodeControlIntentConflict(
                        "node-control request identity conflicts with durable intent"
                    ) from None
                if attempt_invalid:
                    raise NodeControlIntentError(
                        "node-control intent could not be retained"
                    ) from None
                unit_of_work.commit()
                prepared_attempt = attempt
        if prepared_attempt is None:
            raise NodeControlIntentError(
                "node-control intent preparation is unavailable"
            ) from None
        return _preparation(prepared_attempt, replayed=replayed)

    def _prepare_new(
        self,
        unit_of_work: Any,
        command: RequestNodeControlIntent,
    ) -> NodeControlIntendedAttempt:
        stores = unit_of_work.stores
        request = command.request
        workspace_id = command.context.workspace_id
        if request.target.workspace_id.value != workspace_id:
            raise NodeControlIntentConflict(
                "node-control workspace authority conflicts with target"
            ) from None

        try:
            workspace = stores.workspaces.get_for_update(workspace_id)
        except KeyError:
            workspace_missing = True
        else:
            workspace_missing = False
        if workspace_missing:
            raise NodeControlIntentNotFound(
                "node-control workspace was not found"
            ) from None
        lineage = workspace.current_lineage
        if (
            lineage is None
            or lineage.authored_graph_id != request.target.graph_revision.value
        ):
            raise NodeControlIntentConflict(
                "node-control request does not target accepted current graph truth"
            ) from None
        try:
            projection = stores.realized_graphs.get(lineage.realized_projection_id)
            graph = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
        except (KeyError, ValueError):
            projection_missing = True
            projection = None
            graph = None
        else:
            projection_missing = False
        if (
            projection_missing
            or projection is None
            or graph is None
            or projection.workspace_id != workspace_id
            or projection.source_authored_graph_id != lineage.authored_graph_id
        ):
            raise NodeControlIntentConflict(
                "accepted realized graph truth is unavailable"
            ) from None
        gateway_node = _authorize_graph_target(
            graph,
            command.gateway_node_id,
            request,
        )

        transit_key = _select_key(
            stores,
            workspace_id,
            DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
        )
        workload_key = _select_key(
            stores,
            workspace_id,
            DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
        )
        if (
            transit_key.registration_id == workload_key.registration_id
            or transit_key.public_key.fingerprint_sha256
            == workload_key.public_key.fingerprint_sha256
            or transit_key.private_key_reference == workload_key.private_key_reference
        ):
            raise NodeControlIntentConflict(
                "node-control signing authorities must be distinct"
            ) from None

        invalid_grant = False
        try:
            issued_at = self._epoch_clock()
            requested_at = self._clock()
            attempt_id = self._id_factory()
            transit_jti = self._id_factory()
            workload_jti = self._id_factory()
            expires_at = issued_at + self._grant_lifetime_seconds
            transit_grant = DelegatedGatewayNodeControlTransitGrant(
                profile=DelegatedGatewayNodeControlTransitGrantProfile.V1,
                canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
                purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                issuer=transit_key.issuer,
                key_id=transit_key.key_id,
                attempt_id=attempt_id,
                workspace_id=request.target.workspace_id,
                graph_revision=request.target.graph_revision,
                gateway_node_id=command.gateway_node_id,
                target=request.target,
                variable_name=request.variable_name,
                operation=request.operation,
                command_codec=request.command_codec,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                request_digest=request.canonical_digest(),
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=expires_at,
                jti=transit_jti,
            )
            workload_grant = DelegatedWorkloadNodeControlGrant(
                issuer=workload_key.issuer,
                key_id=workload_key.key_id,
                audience=workload_node_control_audience(request.target),
                target=request.target,
                variable_name=request.variable_name,
                operation=request.operation,
                command_codec=request.command_codec,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                request_digest=request.canonical_digest(),
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=expires_at,
                jti=workload_jti,
            )
        except Exception:
            invalid_grant = True
        if invalid_grant:
            raise NodeControlIntentError(
                "node-control unsigned grant construction failed"
            ) from None

        transit_use = _authorize_key_use(
            unit_of_work,
            command,
            attempt_id,
            requested_at,
            transit_key,
            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
        )
        workload_use = _authorize_key_use(
            unit_of_work,
            command,
            attempt_id,
            requested_at,
            workload_key,
            SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
        )
        return NodeControlIntendedAttempt(
            attempt_id=attempt_id,
            actor_subject=command.context.actor_id,
            current_graph_id=lineage.authored_graph_id,
            current_realized_projection_id=lineage.realized_projection_id,
            gateway_runtime_id=gateway_node.runtime_id,
            transit_key_registration_id=transit_key.registration_id,
            workload_key_registration_id=workload_key.registration_id,
            transit_authorization_id=transit_use.authorization_id,
            workload_authorization_id=workload_use.authorization_id,
            transit_correlation_id=transit_use.correlation_id,
            workload_correlation_id=workload_use.correlation_id,
            intended_at=requested_at,
            request=request,
            transit_grant=transit_grant,
            workload_grant=workload_grant,
        )


def _require_scopes(command: RequestNodeControlIntent) -> None:
    scopes = command.context.granted_scopes
    operation_scope = (
        PolicyScope.NODE_CONTROL_READ
        if command.request.operation is NodeControlOperation.READ_STATE
        else PolicyScope.NODE_CONTROL_APPLY
    )
    required = (
        operation_scope,
        PolicyScope.NODE_CONTROL_EXECUTE,
        PolicyScope.DELEGATION_KEY_USE,
        PolicyScope.SECRET_PROVIDER_USE,
    )
    if any(scope not in scopes for scope in required):
        raise NodeControlIntentAuthorizationDenied(
            "node-control command authority is incomplete"
        ) from None


def _existing_attempt(stores: Any, workspace_id: str, request_id: str):
    try:
        return stores.node_control_attempts.get_by_request_id(
            workspace_id,
            request_id,
        )
    except NodeControlAttemptCorrupt:
        corrupt = True
    if corrupt:
        raise NodeControlIntentError(
            "durable node-control intent is corrupt"
        ) from None
    return None


def _authorize_graph_target(
    graph: DeploymentGraph,
    gateway_node_id: NodeControlGraphReference,
    request: NodeControlCommandRequest,
) -> Node:
    try:
        gateway_node = graph.nodes[gateway_node_id.value]
        gateway_control = gateway_node.provider_socket("control")
        target_node = graph.nodes[request.target.node_id.value]
        target_socket = target_node.provider_socket(
            request.target.provider_socket_name.value
        )
    except KeyError:
        missing = True
        gateway_node = None
        gateway_control = None
        target_node = None
        target_socket = None
    else:
        missing = False
    if (
        missing
        or gateway_node is None
        or gateway_control is None
        or target_node is None
        or target_socket is None
        or gateway_control.protocol is not Protocol.HTTP
        or target_socket.protocol is not Protocol.HTTP
        or gateway_node.runtime_id != target_node.runtime_id
    ):
        raise NodeControlIntentNotFound(
            "node-control gateway target is not declared"
        ) from None
    target_in_map = any(
        edge.provider_role == request.target.node_id.value
        and edge.provider_socket == request.target.provider_socket_name.value
        and edge.protocol is Protocol.HTTP
        for edge in graph.edges.values()
    )
    if not target_in_map:
        raise NodeControlIntentNotFound(
            "node-control target is absent from the gateway map"
        ) from None
    surface = next(
        (
            value
            for value in target_node.block_spec.control_surfaces
            if value.provider_socket_name == request.target.provider_socket_name
        ),
        None,
    )
    variable = (
        None
        if surface is None
        else next(
            (
                value
                for value in surface.variables
                if value.variable_name == request.variable_name
            ),
            None,
        )
    )
    if variable is None:
        raise NodeControlIntentNotFound(
            "node-control variable is not declared"
        ) from None
    try:
        contract = variable.contract_for(request.operation)
    except Exception:
        invalid = True
        contract = None
    else:
        invalid = False
    if invalid or contract is None or contract.command_codec is not request.command_codec:
        raise NodeControlIntentNotFound(
            "node-control operation contract is not declared"
        ) from None
    return gateway_node


def _select_key(
    stores: Any,
    workspace_id: str,
    purpose: DelegationKeyPurpose,
) -> RegisteredDelegationSigningKey:
    try:
        selected = stores.delegation_signing_keys.require_unambiguous_active(
            workspace_id,
            purpose,
        )
    except (
        DelegationSigningKeyConflict,
        DelegationSigningKeyNotFound,
        ValueError,
    ):
        missing = True
        selected = None
    else:
        missing = False
    if missing or selected is None or selected.purpose is not purpose:
        raise NodeControlIntentConflict(
            "node-control signing authority is unavailable"
        ) from None
    return selected


def _authorize_key_use(
    unit_of_work: Any,
    command: RequestNodeControlIntent,
    attempt_id: str,
    requested_at: str,
    key: RegisteredDelegationSigningKey,
    intent: SecretUseIntent,
) -> AuthorizedSecretUse:
    correlation_id = secret_use_correlation_for(
        workspace_id=command.context.workspace_id,
        reference=key.private_key_reference,
        intent=intent,
        actor_subject=command.context.actor_id,
        operation_id=attempt_id,
    )
    try:
        authorized, _provider = authorize_secret_use_in_unit_of_work(
            unit_of_work,
            AuthorizeSecretUse(
                workspace_id=command.context.workspace_id,
                reference=key.private_key_reference,
                intent=intent,
                actor_subject=command.context.actor_id,
                correlation_id=correlation_id,
                requested_at=requested_at,
                actor_scopes=command.context.granted_scopes,
                operation_id=attempt_id,
            ),
        )
    except (SecretProviderRegistrationError, ValueError):
        denied = True
        authorized = None
    else:
        denied = False
    if denied or authorized is None:
        raise NodeControlIntentConflict(
            "node-control signing reference is unavailable"
        ) from None
    return authorized


def _preparation(
    attempt: NodeControlIntendedAttempt,
    *,
    replayed: bool,
) -> NodeControlIntentPreparation:
    return NodeControlIntentPreparation(
        attempt=attempt,
        replayed=replayed,
        transit_signing=DeferredGatewayNodeControlTransitSigningRequest(
            attempt.transit_key_registration_id,
            attempt.transit_authorization_id,
            attempt.transit_grant,
        ),
        workload_signing=DeferredWorkloadNodeControlSigningRequest(
            attempt.workload_key_registration_id,
            attempt.workload_authorization_id,
            attempt.workload_grant,
        ),
    )


__all__ = [
    "DeferredGatewayNodeControlTransitSigningRequest",
    "DeferredWorkloadNodeControlSigningRequest",
    "NodeControlIntentAuthorizationDenied",
    "NodeControlIntentAuthorizationService",
    "NodeControlIntentConflict",
    "NodeControlIntentError",
    "NodeControlIntentNotFound",
    "NodeControlIntentPreparation",
    "RequestNodeControlIntent",
]
