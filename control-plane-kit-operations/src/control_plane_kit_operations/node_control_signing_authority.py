"""Reload committed node-control signing authority without performing effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
from typing import Any

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.node_control import (
    DelegatedWorkloadNodeControlGrant,
    verify_workload_node_control_grant,
    workload_node_control_audience,
)
from control_plane_kit_core.node_control_transit import (
    DelegatedGatewayNodeControlTransitGrant,
    verify_gateway_node_control_transit_grant,
)
from control_plane_kit_core.secrets import SecretResolutionGrant, SecretUseIntent
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyConflict,
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.node_control_attempts import (
    NodeControlAttemptCorrupt,
    NodeControlAttemptError,
    NodeControlIntendedAttempt,
)
from control_plane_kit_operations.node_control_intents import (
    DeferredGatewayNodeControlTransitSigningRequest,
    DeferredWorkloadNodeControlSigningRequest,
)
from control_plane_kit_operations.postgres.node_control_signing_authority_store import (
    _LockedSigningFamily,
    _NodeControlSigningAuthorityStoreError,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderRegistrationError,
    _validate_reference_admission,
    secret_resolution_grant_for,
)


class NodeControlSigningAuthorityError(RuntimeError):
    """Base bounded error for signing-authority reload contracts."""


class NodeControlSigningAuthorityUnavailable(NodeControlSigningAuthorityError):
    """Raised when retained signing authority is absent or no longer current."""


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_ACTOR = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ReloadNodeControlSigningAuthority:
    attempt_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _matches(self.attempt_id, _IDENTIFIER):
            raise NodeControlSigningAuthorityError(
                "node-control signing authority selector is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class DeferredNodeControlSigningRequest:
    """One coherent pair of retained unsigned signing requests."""

    attempt_id: str = field(repr=False)
    actor_subject: str = field(repr=False)
    current_graph_id: str
    current_realized_projection_id: str
    transit: DeferredGatewayNodeControlTransitSigningRequest
    transit_correlation_id: str = field(repr=False)
    transit_public_key_fingerprint_sha256: str = field(repr=False)
    workload: DeferredWorkloadNodeControlSigningRequest
    workload_correlation_id: str = field(repr=False)
    workload_public_key_fingerprint_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        failed = False
        try:
            if not (
                _matches(self.attempt_id, _IDENTIFIER)
                and _matches(self.actor_subject, _ACTOR)
                and _matches(self.current_graph_id, _IDENTIFIER)
                and _matches(self.current_realized_projection_id, _IDENTIFIER)
                and _matches(self.transit_correlation_id, _IDENTIFIER)
                and _matches(self.workload_correlation_id, _IDENTIFIER)
                and _matches(
                    self.transit_public_key_fingerprint_sha256,
                    _DIGEST,
                )
                and _matches(
                    self.workload_public_key_fingerprint_sha256,
                    _DIGEST,
                )
                and type(self.transit)
                is DeferredGatewayNodeControlTransitSigningRequest
                and type(self.workload)
                is DeferredWorkloadNodeControlSigningRequest
                and type(self.transit.grant)
                is DelegatedGatewayNodeControlTransitGrant
                and type(self.workload.grant) is DelegatedWorkloadNodeControlGrant
                and _grants_match(self)
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            failed = True
        if failed:
            raise NodeControlSigningAuthorityError(
                "deferred node-control signing request is incoherent"
            ) from None


@dataclass(frozen=True, slots=True)
class GatewayNodeControlTransitSigningAuthority:
    public_key: DelegationPublicKey = field(repr=False)
    resolution_grant: SecretResolutionGrant = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.public_key, DelegationPublicKey) or not isinstance(
            self.resolution_grant,
            SecretResolutionGrant,
        ):
            raise NodeControlSigningAuthorityError(
                "transit signing authority is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class WorkloadNodeControlSigningAuthority:
    public_key: DelegationPublicKey = field(repr=False)
    resolution_grant: SecretResolutionGrant = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.public_key, DelegationPublicKey) or not isinstance(
            self.resolution_grant,
            SecretResolutionGrant,
        ):
            raise NodeControlSigningAuthorityError(
                "workload signing authority is malformed"
            ) from None


@dataclass(frozen=True, slots=True)
class NodeControlSigningAuthorityPair:
    """Complete reference-only signing authority for one retained attempt."""

    deferred_request: DeferredNodeControlSigningRequest
    transit: GatewayNodeControlTransitSigningAuthority = field(repr=False)
    workload: WorkloadNodeControlSigningAuthority = field(repr=False)

    def __post_init__(self) -> None:
        failed = False
        try:
            if not (
                type(self.deferred_request) is DeferredNodeControlSigningRequest
                and type(self.transit) is GatewayNodeControlTransitSigningAuthority
                and type(self.workload) is WorkloadNodeControlSigningAuthority
                and _pair_matches(self)
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            failed = True
        if failed:
            raise NodeControlSigningAuthorityError(
                "node-control signing authority pair is incoherent"
            ) from None


class NodeControlSigningAuthorityReloadService:
    """Reload current reference-only authority from one locked transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        epoch_clock: Callable[[], int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._epoch_clock = epoch_clock

    def execute(
        self,
        command: ReloadNodeControlSigningAuthority,
    ) -> NodeControlSigningAuthorityPair:
        if type(command) is not ReloadNodeControlSigningAuthority:
            raise NodeControlSigningAuthorityError(
                "reload requires ReloadNodeControlSigningAuthority"
            ) from None

        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            attempt = _attempt(stores, command.attempt_id)
            _require_current_workspace(stores, attempt)
            transit_key = _key(
                stores,
                attempt,
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            )
            workload_key = _key(
                stores,
                attempt,
                DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
            )
            truth = _truth(stores, attempt)
            transit_grant = _resolution(
                attempt,
                transit_key,
                truth.transit,
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
                attempt.transit_authorization_id,
                attempt.transit_correlation_id,
            )
            workload_grant = _resolution(
                attempt,
                workload_key,
                truth.workload,
                SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
                attempt.workload_authorization_id,
                attempt.workload_correlation_id,
            )
            now = self._epoch_clock()
            if type(now) is not int or not 0 <= now < 2**53:
                raise NodeControlSigningAuthorityUnavailable(
                    "node-control signing authority clock is invalid"
                ) from None
            _verify_grants(attempt, transit_key, workload_key, now)
            unit_of_work.commit()

        return _pair(
            attempt,
            transit_key,
            workload_key,
            transit_grant,
            workload_grant,
        )


def _attempt(stores: Any, attempt_id: str) -> NodeControlIntendedAttempt:
    failed = False
    try:
        value = stores.node_control_attempts.get(attempt_id)
    except (KeyError, NodeControlAttemptCorrupt, NodeControlAttemptError):
        failed = True
        value = None
    if failed or type(value) is not NodeControlIntendedAttempt:
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing attempt is unavailable"
        ) from None
    return value


def _require_current_workspace(stores: Any, attempt: NodeControlIntendedAttempt) -> None:
    failed = False
    try:
        workspace = stores.workspaces.get_for_update(attempt.workspace_id)
        lineage = workspace.current_lineage
    except (KeyError, ValueError):
        failed = True
        lineage = None
    if (
        failed
        or lineage is None
        or lineage.authored_graph_id != attempt.current_graph_id
        or lineage.realized_projection_id
        != attempt.current_realized_projection_id
    ):
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing lineage is no longer current"
        ) from None


def _key(
    stores: Any,
    attempt: NodeControlIntendedAttempt,
    purpose: DelegationKeyPurpose,
) -> RegisteredDelegationSigningKey:
    failed = False
    try:
        selected = stores.delegation_signing_keys.require_unambiguous_active(
            attempt.workspace_id,
            purpose,
        )
        retained_id = (
            attempt.transit_key_registration_id
            if purpose is DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT
            else attempt.workload_key_registration_id
        )
        derived_id = delegation_signing_key_registration_id_for(
            workspace_id=selected.workspace_id,
            purpose=selected.purpose,
            issuer=selected.issuer,
            public_key=selected.public_key,
            private_key_reference=selected.private_key_reference,
        )
    except (
        DelegationSigningKeyConflict,
        DelegationSigningKeyNotFound,
        TypeError,
        ValueError,
    ):
        failed = True
        selected = None
        retained_id = None
        derived_id = None
    grant = (
        attempt.transit_grant
        if purpose is DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT
        else attempt.workload_grant
    )
    if (
        failed
        or type(selected) is not RegisteredDelegationSigningKey
        or selected.purpose is not purpose
        or selected.workspace_id != attempt.workspace_id
        or selected.registration_id != retained_id
        or selected.registration_id != derived_id
        or selected.issuer != grant.issuer
        or selected.key_id != grant.key_id
    ):
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing key is unavailable"
        ) from None
    return selected


def _truth(stores: Any, attempt: NodeControlIntendedAttempt):
    failed = False
    try:
        value = stores.node_control_signing_authority.get_for_share(attempt)
    except _NodeControlSigningAuthorityStoreError:
        failed = True
        value = None
    if failed or value is None:
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing authorization is unavailable"
        ) from None
    return value


def _resolution(
    attempt: NodeControlIntendedAttempt,
    key: RegisteredDelegationSigningKey,
    family: _LockedSigningFamily,
    intent: SecretUseIntent,
    authorization_id: str,
    correlation_id: str,
) -> SecretResolutionGrant:
    authorization = family.authorization
    reference = family.reference
    provider = family.provider
    failed = False
    try:
        _validate_reference_admission(reference, provider)
        grant = secret_resolution_grant_for(authorization, provider=provider)
    except SecretProviderRegistrationError:
        failed = True
        grant = None
    if (
        failed
        or type(grant) is not SecretResolutionGrant
        or authorization.authorization_id != authorization_id
        or authorization.workspace_id != attempt.workspace_id
        or authorization.reference_registration_id != reference.registration_id
        or authorization.provider_registration_id != provider.registration_id
        or authorization.reference != reference.reference
        or authorization.reference != key.private_key_reference
        or authorization.intent is not intent
        or authorization.actor_subject != attempt.actor_subject
        or authorization.correlation_id != correlation_id
        or authorization.operation_id != attempt.attempt_id
        or any(
            value is not None
            for value in (
                authorization.session_id,
                authorization.run_id,
                authorization.activity_id,
                authorization.effect_id,
                authorization.probe_id,
            )
        )
        or intent not in reference.allowed_intents
        or intent not in provider.allowed_intents
    ):
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing authorization is unavailable"
        ) from None
    return grant


def _verify_grants(
    attempt: NodeControlIntendedAttempt,
    transit_key: RegisteredDelegationSigningKey,
    workload_key: RegisteredDelegationSigningKey,
    now: int,
) -> None:
    failed = False
    try:
        transit = verify_gateway_node_control_transit_grant(
            attempt.transit_grant,
            attempt.request,
            expected_issuer=transit_key.issuer,
            expected_key_id=transit_key.key_id,
            expected_attempt_id=attempt.attempt_id,
            expected_gateway_node_id=attempt.transit_grant.gateway_node_id,
            now=now,
        )
        workload = verify_workload_node_control_grant(
            attempt.workload_grant,
            attempt.request,
            expected_issuer=workload_key.issuer,
            expected_audience=workload_node_control_audience(
                attempt.request.target
            ),
            now=now,
        )
    except ValueError:
        failed = True
        transit = None
        workload = None
    if (
        failed
        or transit is None
        or workload is None
        or not transit.is_accepted
        or not workload.is_accepted
        or attempt.workload_grant.key_id != workload_key.key_id
    ):
        raise NodeControlSigningAuthorityUnavailable(
            "node-control unsigned grants are unavailable"
        ) from None


def _pair(
    attempt: NodeControlIntendedAttempt,
    transit_key: RegisteredDelegationSigningKey,
    workload_key: RegisteredDelegationSigningKey,
    transit_grant: SecretResolutionGrant,
    workload_grant: SecretResolutionGrant,
) -> NodeControlSigningAuthorityPair:
    failed = False
    try:
        value = NodeControlSigningAuthorityPair(
            deferred_request=DeferredNodeControlSigningRequest(
                attempt_id=attempt.attempt_id,
                actor_subject=attempt.actor_subject,
                current_graph_id=attempt.current_graph_id,
                current_realized_projection_id=(
                    attempt.current_realized_projection_id
                ),
                transit=DeferredGatewayNodeControlTransitSigningRequest(
                    attempt.transit_key_registration_id,
                    attempt.transit_authorization_id,
                    attempt.transit_grant,
                ),
                transit_correlation_id=attempt.transit_correlation_id,
                transit_public_key_fingerprint_sha256=(
                    transit_key.public_key.fingerprint_sha256
                ),
                workload=DeferredWorkloadNodeControlSigningRequest(
                    attempt.workload_key_registration_id,
                    attempt.workload_authorization_id,
                    attempt.workload_grant,
                ),
                workload_correlation_id=attempt.workload_correlation_id,
                workload_public_key_fingerprint_sha256=(
                    workload_key.public_key.fingerprint_sha256
                ),
            ),
            transit=GatewayNodeControlTransitSigningAuthority(
                transit_key.public_key,
                transit_grant,
            ),
            workload=WorkloadNodeControlSigningAuthority(
                workload_key.public_key,
                workload_grant,
            ),
        )
    except NodeControlSigningAuthorityError:
        failed = True
        value = None
    if failed or value is None:
        raise NodeControlSigningAuthorityUnavailable(
            "node-control signing authority could not be reconstructed"
        ) from None
    return value


def _grants_match(value: DeferredNodeControlSigningRequest) -> bool:
    transit = value.transit.grant
    workload = value.workload.grant
    common_transit = (
        transit.target,
        transit.variable_name,
        transit.operation,
        transit.command_codec,
        transit.request_id,
        transit.idempotency_key,
        transit.request_digest,
        transit.issued_at,
        transit.not_before,
        transit.expires_at,
    )
    common_workload = (
        workload.target,
        workload.variable_name,
        workload.operation,
        workload.command_codec,
        workload.request_id,
        workload.idempotency_key,
        workload.request_digest,
        workload.issued_at,
        workload.not_before,
        workload.expires_at,
    )
    return (
        transit.attempt_id == value.attempt_id
        and transit.workspace_id == transit.target.workspace_id
        and transit.graph_revision == transit.target.graph_revision
        and value.current_graph_id == transit.graph_revision.value
        and common_transit == common_workload
    )


def _pair_matches(value: NodeControlSigningAuthorityPair) -> bool:
    deferred = value.deferred_request
    return _family_matches(
        deferred,
        deferred.transit,
        deferred.transit_correlation_id,
        deferred.transit_public_key_fingerprint_sha256,
        value.transit.public_key,
        value.transit.resolution_grant,
        SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
    ) and _family_matches(
        deferred,
        deferred.workload,
        deferred.workload_correlation_id,
        deferred.workload_public_key_fingerprint_sha256,
        value.workload.public_key,
        value.workload.resolution_grant,
        SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
    )


def _family_matches(
    deferred: DeferredNodeControlSigningRequest,
    family_request: object,
    correlation_id: str,
    fingerprint: str,
    public_key: DelegationPublicKey,
    resolution: SecretResolutionGrant,
    intent: SecretUseIntent,
) -> bool:
    grant = family_request.grant
    return (
        type(public_key) is DelegationPublicKey
        and type(resolution) is SecretResolutionGrant
        and public_key.algorithm is DelegationKeyAlgorithm.ED25519
        and public_key.key_id == grant.key_id
        and public_key.fingerprint_sha256 == fingerprint
        and resolution.authorization_id == family_request.authorization_id
        and resolution.workspace_id == grant.target.workspace_id.value
        and resolution.intent is intent
        and resolution.actor_subject == deferred.actor_subject
        and resolution.correlation_id == correlation_id
        and resolution.operation_id == deferred.attempt_id
        and all(
            value is None
            for value in (
                resolution.session_id,
                resolution.run_id,
                resolution.activity_id,
                resolution.effect_id,
                resolution.probe_id,
            )
        )
    )


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


__all__ = [
    "DeferredNodeControlSigningRequest",
    "GatewayNodeControlTransitSigningAuthority",
    "NodeControlSigningAuthorityError",
    "NodeControlSigningAuthorityPair",
    "NodeControlSigningAuthorityReloadService",
    "NodeControlSigningAuthorityUnavailable",
    "ReloadNodeControlSigningAuthority",
    "WorkloadNodeControlSigningAuthority",
]
