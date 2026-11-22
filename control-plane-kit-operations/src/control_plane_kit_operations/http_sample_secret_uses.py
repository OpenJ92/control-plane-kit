"""Transaction suffix only; the reached-check owner establishes current authority.

No commit, lease decision, resolver or external effect is performed here. The
caller must load the original context under its current command/run guards,
observe the lease, commit, and return only after successful physical UoW exit.
"""
import re

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import http_verification_sample_signing_intent_for
from control_plane_kit_operations.delegation_signing_keys import RegisteredDelegationSigningKeyStatus
from control_plane_kit_operations.http_sample_sender_input import (
    bind_http_sample_sender_input, http_sample_signing_binding_for, http_sample_signing_correlation_for,
    http_sample_role_correlation,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, AuthorizedSecretUse, authorize_secret_use_in_unit_of_work,
    authorized_secret_use_for, validate_authorized_secret_use, _validate_reference_admission,
)

_ROLES = (("transit", DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT),
    ("workload", DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE))
_SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE,
    PolicyScope.DELEGATION_KEY_USE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)


class HttpSampleSecretUseConflict(ValueError):
    """The original two-role transaction cannot be established."""


def _require(condition):
    if condition is not True:
        raise HttpSampleSecretUseConflict("HTTP sample secret-use pair is unavailable")


def http_sample_authorization_command(*, context, key, actor_scopes, requested_at):
    """Same sample-specific identity for issuance and later resolution admission."""
    correlation, probe = http_sample_signing_binding_for(**context,
        purpose=key.purpose, key_registration_id=key.registration_id)
    return AuthorizeSecretUse(workspace_id=context["compatibility"].workspace_id,
        reference=key.private_key_reference, intent=http_verification_sample_signing_intent_for(key.purpose),
        actor_subject=context["actor_subject"], correlation_id=correlation, requested_at=requested_at,
        actor_scopes=actor_scopes, operation_id=context["operation_id"], session_id=context["session_id"],
        run_id=context["attempt"].run_id.value, activity_id=context["attempt"].activity_id,
        effect_id=context["effect_id"], probe_id=probe)


def _registration_id(authorized):
    _require(type(authorized) is AuthorizedSecretUse)
    validate_authorized_secret_use(authorized)
    match = re.fullmatch(r"http-sample\.v1:[0-9a-f]{64}:(dkey_[0-9a-f]{64})", authorized.probe_id or "")
    _require(match is not None)
    return match.group(1)


def _pair(unit, context, actor_scopes, requested_at, *, allow_create, authorization_id=None):
    _require(type(actor_scopes) is tuple and all(type(scope) is PolicyScope for scope in actor_scopes)
        and all(scope in actor_scopes for scope in _SCOPES))
    stores = unit.stores
    workspace = context["compatibility"].workspace_id
    correlations = {role: http_sample_signing_correlation_for(**context, purpose=purpose) for role, purpose in _ROLES}
    for correlation in sorted(correlations.values()):
        stores.secret_use_authorizations.lock_correlation(workspace, correlation)
    rows = stores.secret_use_authorizations.for_correlations(workspace, tuple(correlations.values()))
    _require(len(rows) in (0, 2) and (allow_create or len(rows) == 2))
    by_correlation = {row.correlation_id: row for row in rows}
    _require(set(by_correlation) == (set(correlations.values()) if rows else set()))
    uses = {role: by_correlation[correlations[role]] for role, _ in _ROLES} if rows else {}
    keys = {}
    for role, purpose in _ROLES:
        # No active selection occurs on the retained path, including retirement.
        keys[role] = (stores.delegation_signing_keys.get_by_registration(workspace, purpose,
            _registration_id(uses[role]), for_share=True) if rows else
            stores.delegation_signing_keys.require_unambiguous_active(workspace, purpose))
        _require(keys[role].status is RegisteredDelegationSigningKeyStatus.ACTIVE)
    _require(keys["transit"].private_key_reference != keys["workload"].private_key_reference
        and keys["transit"].public_key.fingerprint_sha256 != keys["workload"].public_key.fingerprint_sha256)

    # Pin ALL references before ANY provider row. A single-role authorizer
    # follows reference -> provider, so waiting for B cannot happen holding P.
    references = {}
    for role in sorted(keys, key=lambda role: keys[role].private_key_reference.reference_id):
        references[role] = stores.secret_references.get_active_for_update(workspace, keys[role].private_key_reference)
        if rows:
            _require(references[role].registration_id == uses[role].reference_registration_id
                and references[role].provider_registration_id == uses[role].provider_registration_id)
    providers = {identity: stores.secret_providers.require_active_registration_for_update(workspace, identity)
        for identity in sorted({reference.provider_registration_id for reference in references.values()})}
    for role, purpose in _ROLES:
        reference = references[role]
        _validate_reference_admission(reference, providers[reference.provider_registration_id])
        _require(http_verification_sample_signing_intent_for(purpose) in reference.allowed_intents)
    if rows and authorization_id is not None:
        _require(sum(use.authorization_id == authorization_id for use in uses.values()) == 1)
    commands = {role: http_sample_authorization_command(context=context, key=keys[role], actor_scopes=actor_scopes,
        requested_at=uses[role].requested_at if rows else requested_at) for role, _ in _ROLES}
    candidates = {role: authorized_secret_use_for(commands[role], reference=references[role],
        provider=providers[references[role].provider_registration_id]) for role, _ in _ROLES}
    if rows:
        _require(candidates == uses)
    # Prove BOTH installed public-key coverages before the first use insert.
    pair = bind_http_sample_sender_input(**context, **{name: value for role, _ in _ROLES for name, value in (
        (role + "_key", keys[role]), (role + "_use", candidates[role]),
        (role + "_provider", providers[references[role].provider_registration_id]))})
    for role, _ in _ROLES:
        if authorization_id is not None and uses[role].authorization_id != authorization_id:
            continue
        authorized, provider = authorize_secret_use_in_unit_of_work(unit, commands[role])
        reference = references[role]
        _require(provider == providers[reference.provider_registration_id]
            and authorized.reference_registration_id == reference.registration_id)
        _require(authorized == candidates[role])
    return pair


def _checked(action):
    try:
        return action()
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        pass
    raise HttpSampleSecretUseConflict("HTTP sample secret-use pair is unavailable")


def prepare_http_sample_uses_in_unit_of_work(unit, *, context, actor_scopes, requested_at):
    """Atomic metadata creation/reload inside the caller's guarded transaction."""
    return _checked(lambda: _pair(unit, context, actor_scopes, requested_at, allow_create=True))


def reauthorize_http_sample_use_in_unit_of_work(unit, *, context, actor_scopes, authorization_id):
    """Reauthorize the exact original role; missing evidence cannot create rows."""
    def reauthorize():
        _require(type(authorization_id) is str and re.fullmatch(r"suse_[0-9a-f]{64}", authorization_id) is not None)
        pair = _pair(unit, context, actor_scopes, None, allow_create=False, authorization_id=authorization_id)
        return next(grant for grant in (pair.transit_grant, pair.workload_grant) if grant.authorization_id == authorization_id)
    return _checked(reauthorize)


def read_http_sample_uses(stores, *, context):
    """Validate original references coherently without current lifecycle authority."""
    def read():
        workspace = context["compatibility"].workspace_id
        correlations = {role: http_sample_signing_correlation_for(**context, purpose=purpose)
            for role, purpose in _ROLES}
        rows = stores.secret_use_authorizations.for_correlations(workspace, tuple(correlations.values()))
        _require(len(rows) in (0, 2))
        if not rows:
            return None, ()
        uses = {role: next(row for row in rows if row.correlation_id == correlation)
            for role, correlation in correlations.items()}
        supplied = {}
        for role, purpose in _ROLES:
            use = uses[role]
            key = stores.delegation_signing_keys.get_by_registration(workspace, purpose, _registration_id(use))
            reference = stores.secret_references.get_by_registration(workspace, use.reference_registration_id)
            provider = stores.secret_providers.get_by_registration(workspace, use.provider_registration_id)
            command = http_sample_authorization_command(context=context, key=key,
                actor_scopes=(), requested_at=use.requested_at)
            _require(authorized_secret_use_for(command, reference=reference, provider=provider) == use)
            supplied.update({role + "_key": key, role + "_use": use, role + "_provider": provider})
        return bind_http_sample_sender_input(**context, **supplied), tuple(uses[role] for role, _ in _ROLES)
    return _checked(read)


def require_http_sample_use_owner(stores, *, authorized, marker, attempt, request, actor_subject):
    """Validate a recorded role's owner, never its check-level meaning or authority."""
    registration_id = _registration_id(authorized)
    purposes = tuple(purpose for _, purpose in _ROLES
        if http_verification_sample_signing_intent_for(purpose) is authorized.intent)
    _require(len(purposes) == 1)
    purpose, = purposes
    key = stores.delegation_signing_keys.get_by_registration(marker.workspace_id, purpose, registration_id)
    target_digest = authorized.probe_id.split(":")[1]
    correlation = http_sample_role_correlation(workspace_id=marker.workspace_id,
        attempt=attempt, operation_id=request.identity.request_id, session_id=request.identity.session_id,
        actor_subject=actor_subject, effect_id=marker.original_event_id,
        request_fingerprint=marker.request_fingerprint, target_digest=target_digest, purpose=purpose)
    _require((authorized.workspace_id, authorized.run_id, authorized.activity_id, authorized.effect_id,
        authorized.operation_id, authorized.session_id, authorized.actor_subject, authorized.reference,
        authorized.correlation_id, authorized.requested_at) == (
        marker.workspace_id, attempt.run_id.value, attempt.activity_id, marker.original_event_id,
        request.identity.request_id, request.identity.session_id, actor_subject, key.private_key_reference,
        correlation, marker.created_at))


__all__ = ["HttpSampleSecretUseConflict", "http_sample_authorization_command",
    "prepare_http_sample_uses_in_unit_of_work", "reauthorize_http_sample_use_in_unit_of_work",
    "read_http_sample_uses"]
