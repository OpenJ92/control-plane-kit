"""Original SQL roles inside the caller's reached-check transaction; no I/O."""
from dataclasses import fields
from datetime import datetime, timezone
from hashlib import sha256
import rfc8785

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import NodeControlCanonicalization
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.receiver_health_reads import DelegatedWorkloadReceiverHealthReadGrant, DelegatedWorkloadReceiverHealthReadGrantProfile
from control_plane_kit_core.receiver_health_transit import DelegatedGatewayReceiverHealthReadTransitGrant, DelegatedGatewayReceiverHealthReadTransitGrantProfile
from control_plane_kit_core.receiver_identity import receiver_node_control_audience
from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
from control_plane_kit_core.secret_selections import secret_selection_from_descriptor
from control_plane_kit_core.secrets import health_signing_intent_for
from control_plane_kit_operations.delegation_signing_keys import RegisteredDelegationSigningKeyStatus, DelegationSigningKeyNotFound
from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
from control_plane_kit_operations.health_signing_authority import GatewayNodeHealthReadTransitSigningAuthority, WorkloadNodeHealthReadSigningAuthority
from control_plane_kit_operations.managed_verification_execution import PreparedPostgresReadinessCheck
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.records import ExecutionRequestRecord, _canonical_timestamp_instant
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, AuthorizeSecretVersionUse, AuthorizedSecretUse, AuthorizedSecretVersionUse,
    authorize_secret_use_in_unit_of_work, authorize_secret_version_use_in_unit_of_work,
    authorized_secret_use_for, validate_authorized_secret_use, secret_resolution_grant_for,
    _validate_reference_admission, _version_authorization_fingerprint,
)

_ROLES = (("transit", DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT),
          ("workload", DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ))
_SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
           PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)


class PostgresReadinessUseConflict(ValueError):
    """Fixed refusal with no protected references or retained owner errors."""


def _require(condition):
    if condition is not True:
        raise ValueError


def _checked(call):
    try:
        return call()
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError, OverflowError):
        pass
    raise PostgresReadinessUseConflict("original PostgreSQL readiness uses are unavailable")


def postgres_readiness_use_correlations(*, binding):
    """Key-independent role coordinates; no current active-key selection."""
    binding.__post_init__()
    document = dict(domain="cpk.original-postgres-readiness-role.v1", request=binding.request().descriptor(),
        binding=binding.descriptor())
    return {role: "sql-readiness:" + sha256(rfc8785.dumps({**document, "role": role})).hexdigest()
        for role in ("password", "transit", "workload")}


def _original_scope(context):
    binding, original, request = context["binding"], context["original"], context["execution_request"]
    _require(type(original) is EffectAttemptIntentRecord and type(request) is ExecutionRequestRecord)
    original.__post_init__()
    request.__post_init__()
    _require(original.identity == binding.attempt and original.original_start_event.event_id == binding.wait.effect_id
        and runtime_effect_intent_fingerprint(original.intent) == binding.descriptor()["request_fingerprint"]
        and request.identity.request_id == binding.wait.source.request_id
        and request.identity.workspace_id == binding.wait.source.workspace_id
        and request.identity.plan_id == binding.wait.source.plan_id)
    timestamp = original.original_start_event.occurred_at
    instant = _canonical_timestamp_instant(timestamp, "original SQL start")
    delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    _require((delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000 == binding.started_at_epoch_ms
        and context["session_id"] == request.identity.session_id
        and context["requested_at"] == timestamp)
    return request.identity.session_id, timestamp


def _commands(context, keys, scopes):
    binding = context["binding"]
    session, timestamp = _original_scope(context)
    common = dict(workspace_id=binding.wait.source.workspace_id, actor_subject=context["actor_subject"],
        session_id=session, requested_at=timestamp, actor_scopes=scopes,
        run_id=binding.attempt.run_id.value, activity_id=binding.attempt.activity_id)
    correlations = postgres_readiness_use_correlations(binding=binding)
    password = AuthorizeSecretVersionUse(**common, correlation_id=correlations["password"],
        selection=secret_selection_from_descriptor(binding.descriptor()["credential_selection"]),
        operation_id=binding.wait.source.request_id, effect_id=binding.wait.effect_id,
        request_fingerprint=binding.descriptor()["request_fingerprint"],
        selection_fingerprint=binding.receipt.selection_fingerprint)
    return {"password": password, **{role: AuthorizeSecretUse(**common,
        reference=keys[role].private_key_reference, intent=health_signing_intent_for(purpose),
        correlation_id=correlations[role], operation_id=health_effect_attempt_wire_id(binding.attempt))
        for role, purpose in _ROLES}}


def _installed(context, role):
    if role == "transit":
        trust = context["route"].gateway_trust
        return trust.issuer, trust.public_keys
    families = tuple(family for family in context["binding"].receiver.verifiers
        if family.purpose is DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
    _require(len(families) == 1)
    return families[0].issuer, families[0].public_keys


def _historical_key(stores, context, role, purpose, use):
    _require(type(use) is AuthorizedSecretUse)
    validate_authorized_secret_use(use)
    issuer, public_keys = _installed(context, role)
    matches = []
    for public in public_keys:
        try:
            key = stores.delegation_signing_keys.get(use.workspace_id, purpose, issuer, public.key_id)
        except DelegationSigningKeyNotFound:
            continue
        if key.public_key == public and key.private_key_reference == use.reference:
            matches.append(key)
    # Count immutable matches before lifecycle. Never prefer an active identity
    # over another matching retained identity, or fall back to a replacement.
    _require(len(matches) == 1)
    return matches[0]


def _rows(stores, context, *, lock=False):
    binding = context["binding"]
    workspace = binding.wait.source.workspace_id
    _original_scope(context)
    correlations = postgres_readiness_use_correlations(binding=binding)
    if lock:
        for correlation in sorted(correlations.values()):
            stores.secret_use_authorizations.lock_correlation(workspace, correlation)
    rows = stores.secret_use_authorizations.for_three_correlations(workspace, tuple(correlations.values()))
    _require(len(rows) in (0, 3))
    by_correlation = {row.correlation_id: row for row in rows}
    _require(set(by_correlation) == (set(correlations.values()) if rows else set()))
    return {role: by_correlation[correlation] for role, correlation in correlations.items()} if rows else {}


def _validate_use(use, command, reference, provider):
    validate_authorized_secret_use(use)
    expected = AuthorizedSecretVersionUse if type(command) is AuthorizeSecretVersionUse else AuthorizedSecretUse
    _require(type(use) is expected and use.reference_registration_id == reference.registration_id
        and use.provider_registration_id == provider.registration_id
        and reference.workspace_id == provider.workspace_id == use.workspace_id
        and reference.provider_registration_id == provider.registration_id
        and reference.reference == (use.selection.reference if expected is AuthorizedSecretVersionUse else use.reference))
    for item in fields(command):
        if item.name != "actor_scopes":
            _require(getattr(use, item.name) == getattr(command, item.name))
    if expected is AuthorizedSecretUse:
        _require(use == authorized_secret_use_for(command, reference=reference, provider=provider))


def _prepared(context, keys, uses, providers):
    binding = context["binding"]
    request = binding.request()
    password = uses["password"]
    password_grant = SecretVersionResolutionGrant(**{item.name: getattr(password, item.name)
        for item in fields(password) if item.name != "requested_at"},
        endpoint_reference=providers[password.provider_registration_id].endpoint_reference,
        credential_reference=providers[password.provider_registration_id].credential_reference)
    signers = {role: constructor(keys[role].public_key,
        secret_resolution_grant_for(uses[role], provider=providers[uses[role].provider_registration_id]))
        for role, constructor in (("transit", GatewayNodeHealthReadTransitSigningAuthority),
                                 ("workload", WorkloadNodeHealthReadSigningAuthority))}
    common = dict(canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1, target=request.target,
        authority_context=request.authority_context, kind=request.kind, declaration_identity=request.declaration_identity,
        request_id=request.request_id, request_digest=request.canonical_digest(),
        issued_at=binding.started_at_epoch_ms // 1000, not_before=binding.started_at_epoch_ms // 1000,
        expires_at=binding.deadline_epoch_ms // 1000)
    def claims(role):
        return dict(issuer=keys[role].issuer, key_id=keys[role].public_key.key_id,
            jti="sql-" + sha256(uses[role].correlation_id.encode()).hexdigest())
    transit = DelegatedGatewayReceiverHealthReadTransitGrant(**common, **claims("transit"),
        profile=DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2,
        purpose=DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
        attempt_id=health_effect_attempt_wire_id(binding.attempt), gateway_target=context["route"].gateway_target)
    workload = DelegatedWorkloadReceiverHealthReadGrant(**common, **claims("workload"),
        profile=DelegatedWorkloadReceiverHealthReadGrantProfile.V2,
        purpose=DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, audience=receiver_node_control_audience(request.target))
    return PreparedPostgresReadinessCheck(binding, password_grant, signers["transit"], signers["workload"],
        transit, workload, context["route"])


def read_postgres_readiness_uses(stores, *, context):
    """Historical metadata only: inactive originals remain inspectable, not usable."""
    def read():
        uses = _rows(stores, context)
        if not uses:
            return None, ()
        keys = {role: _historical_key(stores, context, role, purpose, uses[role]) for role, purpose in _ROLES}
        commands = _commands(context, keys, ())
        providers = {}
        for role, use in uses.items():
            reference = stores.secret_references.get_by_registration(use.workspace_id, use.reference_registration_id)
            provider = stores.secret_providers.get_by_registration(use.workspace_id, use.provider_registration_id)
            _validate_use(use, commands[role], reference, provider)
            providers[provider.registration_id] = provider
        return _prepared(context, keys, uses, providers), tuple(uses[role] for role in ("password", "transit", "workload"))
    return _checked(read)


def prepare_postgres_readiness_uses_in_unit_of_work(unit, *, context, actor_scopes, allow_create=True):
    """Caller owns current check/lease guards, commit and physical transaction exit."""
    def prepare():
        _require(type(allow_create) is bool and type(actor_scopes) is tuple and all(scope in actor_scopes for scope in _SCOPES))
        stores, binding = unit.stores, context["binding"]
        workspace = binding.wait.source.workspace_id
        uses = _rows(stores, context, lock=True)
        _require(bool(uses) or allow_create is True)
        keys = {}
        for role, purpose in _ROLES:
            if uses:
                original = _historical_key(stores, context, role, purpose, uses[role])
                key = stores.delegation_signing_keys.get_by_registration(workspace, purpose,
                    original.registration_id, for_share=True)
                _require(key.same_identity_as(original))
            else:
                key = stores.delegation_signing_keys.require_unambiguous_active(workspace, purpose)
            _require(key.status is RegisteredDelegationSigningKeyStatus.ACTIVE)
            keys[role] = key
        commands = _commands(context, keys, actor_scopes)
        references = {}
        for role in sorted(commands, key=lambda role: (commands[role].selection.reference.reference_id
                if role == "password" else commands[role].reference.reference_id)):
            command = commands[role]
            reference = command.selection.reference if role == "password" else command.reference
            references[role] = stores.secret_references.get_active_for_update(workspace, reference)
        providers = {identity: stores.secret_providers.require_active_registration_for_update(workspace, identity)
            for identity in sorted({reference.provider_registration_id for reference in references.values()})}
        candidates = {}
        for role, command in commands.items():
            reference = references[role]
            provider = providers[reference.provider_registration_id]
            _validate_reference_admission(reference, provider)
            _require((command.selection.intent if role == "password" else command.intent) in reference.allowed_intents)
            if role == "password":
                fingerprint = _version_authorization_fingerprint(command, reference.registration_id, provider.registration_id)
                candidate = AuthorizedSecretVersionUse(**{item.name: getattr(command, item.name)
                    for item in fields(command) if item.name != "actor_scopes"}, authorization_id="suse_" + fingerprint,
                    reference_registration_id=reference.registration_id, provider_registration_id=provider.registration_id,
                    intent_fingerprint=fingerprint)
            else:
                candidate = authorized_secret_use_for(command, reference=reference, provider=provider)
            if uses:
                _require(candidate == uses[role])
            candidates[role] = candidate
        for role, purpose in _ROLES:
            original = _historical_key(stores, context, role, purpose, candidates[role])
            _require(original.same_identity_as(keys[role]))
        # Both installed key/issuer/receiver joins precede the first use insert.
        prepared = _prepared(context, keys, candidates, providers)
        for role, command in commands.items():
            authorize = authorize_secret_version_use_in_unit_of_work if role == "password" else authorize_secret_use_in_unit_of_work
            retained, _ = authorize(unit, command)
            _require(retained == candidates[role])
        return prepared
    return _checked(prepare)


__all__ = ["PostgresReadinessUseConflict", "postgres_readiness_use_correlations",
    "read_postgres_readiness_uses", "prepare_postgres_readiness_uses_in_unit_of_work"]
