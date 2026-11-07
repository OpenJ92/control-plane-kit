"""One-check congruence of supplied records; no loading, admission or signing.

The caller must obtain committed evidence from its owner. Constructing matching
records here cannot establish provenance, current permission or reached-check
admission. Both execution denials remain outside this pure calculation.
"""
from dataclasses import dataclass, replace
from hashlib import sha256
import re

import rfc8785

from control_plane_kit_core._node_control_public_wire import digest_violation, reference_violation
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.http_verification_samples import http_verification_check_digest
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_core.secrets import SecretResolutionGrant, http_verification_sample_signing_intent_for
from control_plane_kit_core.verification import HttpCheck
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey, delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.http_sample_compatibility import HttpSampleCompatibility, GatewayHttpSampleTrust
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse, RegisteredSecretProvider, secret_provider_registration_id_for,
    secret_resolution_grant_for, validate_authorized_secret_use,
    _reference_is_within, _require_identifier, _require_correlation_identifier,
)

_TRANSIT = DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT
_WORKLOAD = DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)


class HttpSampleSenderInputUnavailable(ValueError):
    """Fixed refusal; candidate metadata and underlying errors are not exposed."""


def _require(condition):
    if condition is not True:
        raise ValueError


def _checked(action):
    try:
        return action()
    except _INPUT_ERRORS:
        pass
    raise HttpSampleSenderInputUnavailable("HTTP sample sender input is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class HttpSampleSenderInput:
    """Reference-only handoff, never a permit or proof of committed provenance."""

    compatibility: HttpSampleCompatibility
    check: HttpCheck
    attempt: EffectAttemptIdentity
    operation_id: str
    session_id: str
    actor_subject: str
    effect_id: str
    request_fingerprint: str
    transit_key: RegisteredDelegationSigningKey
    transit_grant: SecretResolutionGrant
    workload_key: RegisteredDelegationSigningKey
    workload_grant: SecretResolutionGrant


def _context(compatibility, plan, check_id, attempt, operation_id, session_id,
        actor_subject, effect_id, request_fingerprint):
    _require(type(compatibility) is HttpSampleCompatibility and type(plan) is ActivityPlanRecord
        and replace(plan) == plan and type(attempt) is EffectAttemptIdentity and replace(attempt) == attempt)
    _require(plan.status is ActivityPlanStatus.PLANNED
        and plan.derivation_profile is PlanDerivationProfile.MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1
        and plan.plan.ready_for_execution
        and (plan.plan_id, plan.desired_graph_id, plan.desired_realized_projection_id, plan.session_id)
            == (compatibility.plan_id, compatibility.desired_graph_id, compatibility.projection_id, session_id))
    _require_identifier(actor_subject, "actor_subject")
    _require_correlation_identifier(operation_id, "operation_id")
    _require_correlation_identifier(session_id, "session_id")
    for value in (attempt.run_id.value, attempt.activity_id, effect_id, check_id):
        _require(type(value) is str and reference_violation(value) is None)
    NodeControlAuthorityContext(compatibility.desired_graph_id, compatibility.projection_id)
    _require(type(request_fingerprint) is str and digest_violation(request_fingerprint) is None)
    receiver = compatibility.receiver
    _require(replace(receiver) == receiver)
    configured = receiver.receiver_configuration
    gateway = compatibility.gateway_trust
    _require(type(gateway) is GatewayHttpSampleTrust and replace(gateway) == gateway
        and configured.target.workspace_id.value == gateway.target.workspace_id.value == compatibility.workspace_id
        and configured.target.runtime_id == gateway.target.runtime_id
        and compatibility.route.target == configured.target
        and compatibility.route.declaration == configured.declaration)
    activities = tuple(item for item in plan.plan.activities if item.activity_id.value == attempt.activity_id)
    _require(len(activities) == 1 and type(activities[0].operation) is WaitForHealthy
        and type(activities[0].operation.target) is NodeTarget
        and activities[0].operation.target.node_id == configured.target.node_id.value)
    checks = tuple(entry.check for entry in receiver.checks if entry.check.check_id == check_id)
    _require(len(checks) == 1)
    return checks[0]


def _role_identity(context, purpose):
    check = _context(**context)
    _require(type(purpose) is DelegationKeyPurpose and purpose in (_TRANSIT, _WORKLOAD))
    compatibility = context["compatibility"]
    target = compatibility.gateway_trust.target if purpose is _TRANSIT else compatibility.receiver.configuration.target
    target_digest = sha256(rfc8785.dumps(dict(domain="cpk.http-sample-role-target.v1",
        purpose=purpose.value, target=target.descriptor(),
        workload_target=compatibility.receiver.configuration.target.descriptor(),
        gateway_target=compatibility.gateway_trust.target.descriptor(),
        declaration_identity=compatibility.receiver.configuration.declaration_identity,
        route_id=compatibility.route.target_id, plan_id=compatibility.plan_id,
        authored_graph_id=compatibility.desired_graph_id, projection_id=compatibility.projection_id,
        check_id=check.check_id, check_digest=http_verification_check_digest(check)))).hexdigest()
    return http_sample_role_correlation(workspace_id=compatibility.workspace_id,
        attempt=context["attempt"], operation_id=context["operation_id"], session_id=context["session_id"],
        actor_subject=context["actor_subject"], effect_id=context["effect_id"],
        request_fingerprint=context["request_fingerprint"], target_digest=target_digest,
        purpose=purpose), target_digest


def http_sample_role_correlation(*, workspace_id, attempt, operation_id, session_id,
        actor_subject, effect_id, request_fingerprint, target_digest, purpose):
    """Internal correlation relation; a supplied digest does not attest a check."""
    correlation = sha256(rfc8785.dumps(dict(domain="cpk.http-sample-role-use.v1",
        workspace_id=workspace_id, attempt=attempt.descriptor(), operation_id=operation_id,
        session_id=session_id, actor_subject=actor_subject, effect_id=effect_id,
        request_fingerprint=request_fingerprint, target_digest=target_digest, purpose=purpose.value))).hexdigest()
    return "http-sample-use:" + correlation


def _binding(context, purpose, key_registration_id):
    _require(type(key_registration_id) is str and re.fullmatch(r"dkey_[0-9a-f]{64}", key_registration_id) is not None)
    correlation, target_digest = _role_identity(context, purpose)
    return correlation, "http-sample.v1:" + target_digest + ":" + key_registration_id


def http_sample_signing_correlation_for(*, compatibility, plan, check_id, attempt, operation_id,
        session_id, actor_subject, effect_id, request_fingerprint, purpose):
    """Locate original role evidence without selecting a current/replacement key."""
    context = dict(compatibility=compatibility, plan=plan, check_id=check_id, attempt=attempt,
        operation_id=operation_id, session_id=session_id, actor_subject=actor_subject,
        effect_id=effect_id, request_fingerprint=request_fingerprint)
    return _checked(lambda: _role_identity(context, purpose)[0])


def http_sample_signing_binding_for(*, compatibility, plan, check_id, attempt, operation_id,
        session_id, actor_subject, effect_id, request_fingerprint, purpose, key_registration_id):
    """Pure correlation/probe recipe, not creation or lookup of an authorization."""
    context = dict(compatibility=compatibility, plan=plan, check_id=check_id, attempt=attempt,
        operation_id=operation_id, session_id=session_id, actor_subject=actor_subject,
        effect_id=effect_id, request_fingerprint=request_fingerprint)
    return _checked(lambda: _binding(context, purpose, key_registration_id))


def _role(context, key, use, provider, purpose, verifier):
    _require(type(key) is RegisteredDelegationSigningKey and replace(key) == key
        and type(use) is AuthorizedSecretUse and type(provider) is RegisteredSecretProvider and replace(provider) == provider)
    validate_authorized_secret_use(use)
    workspace = context["compatibility"].workspace_id
    _require(key.workspace_id == use.workspace_id == provider.workspace_id == workspace
        and key.purpose is purpose and key.issuer == verifier.issuer
        and key.registration_id == delegation_signing_key_registration_id_for(workspace_id=workspace,
            purpose=purpose, issuer=key.issuer, public_key=key.public_key, private_key_reference=key.private_key_reference))
    selected = tuple(candidate for candidate in verifier.public_keys if candidate.key_id == key.key_id)
    _require(len(selected) == 1 and selected[0] == key.public_key
        and selected[0].fingerprint_sha256 == key.public_key.fingerprint_sha256)
    correlation, probe = _binding(context, purpose, key.registration_id)
    _require(use.reference == key.private_key_reference
        and use.intent is http_verification_sample_signing_intent_for(purpose)
        and (use.actor_subject, use.operation_id, use.session_id, use.run_id, use.activity_id,
            use.effect_id, use.correlation_id, use.probe_id)
            == (context["actor_subject"], context["operation_id"], context["session_id"],
                context["attempt"].run_id.value, context["attempt"].activity_id,
                context["effect_id"], correlation, probe))
    _require(provider.registration_id == secret_provider_registration_id_for(workspace_id=provider.workspace_id,
        provider_id=provider.provider_id, provider_kind=provider.provider_kind, display_name=provider.display_name,
        endpoint_reference=provider.endpoint_reference, credential_reference=provider.credential_reference,
        allowed_reference_prefixes=provider.allowed_reference_prefixes, allowed_intents=provider.allowed_intents,
        supersedes_registration_id=provider.supersedes_registration_id, metadata=provider.metadata))
    _require(use.intent in provider.allowed_intents
        and any(_reference_is_within(use.reference, prefix) for prefix in provider.allowed_reference_prefixes))
    # Existing projection checks provider-registration identity. Its grant keeps
    # reference-registration identity; no reference row or current lifecycle is loaded.
    return secret_resolution_grant_for(use, provider=provider)


def bind_http_sample_sender_input(*, compatibility, plan, check_id, attempt, operation_id,
        session_id, actor_subject, effect_id, request_fingerprint,
        transit_key, transit_use, transit_provider, workload_key, workload_use, workload_provider):
    """Join supplied role facts only; the caller owns their committed provenance.

    No stores, permission decision, lease, secret resolver or transport is used.
    Current eligibility and atomic issuance belong to the reached-check owner.
    """
    context = dict(compatibility=compatibility, plan=plan, check_id=check_id, attempt=attempt,
        operation_id=operation_id, session_id=session_id, actor_subject=actor_subject,
        effect_id=effect_id, request_fingerprint=request_fingerprint)

    def bind():
        check = _context(**context)
        transit = _role(context, transit_key, transit_use, transit_provider, _TRANSIT, compatibility.gateway_trust)
        workload = _role(context, workload_key, workload_use, workload_provider, _WORKLOAD,
            compatibility.receiver.configuration.verifier)
        _require(transit_key.public_key.fingerprint_sha256 != workload_key.public_key.fingerprint_sha256
            and transit_key.private_key_reference != workload_key.private_key_reference
            and transit.authorization_id != workload.authorization_id and transit.correlation_id != workload.correlation_id)
        return HttpSampleSenderInput(compatibility, check, attempt, operation_id, session_id,
            actor_subject, effect_id, request_fingerprint, transit_key, transit, workload_key, workload)

    return _checked(bind)


__all__ = ["HttpSampleSenderInputUnavailable", "HttpSampleSenderInput",
    "http_sample_signing_correlation_for", "http_sample_signing_binding_for", "bind_http_sample_sender_input"]
