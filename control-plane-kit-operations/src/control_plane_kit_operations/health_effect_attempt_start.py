"""Trusted health admission values; unsigned evidence never permits dispatch."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import re

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind,
    TrustedCommandContext, WorkspaceGrant,
)
from control_plane_kit_core.planning import ObserveNodeHealth
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_start import (
    ExistingAttempt, NewlyStarted, StartEffectAttempt, _valid_start_command,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.health_effect_preparations import (
    HealthEffectPreparationCodec, HealthEffectPreparationRecord,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import InvalidOperationCommand


@dataclass(frozen=True, slots=True)
class StartHealthEffectAttempt:
    """Current authenticated actor authority, separate from the worker fence."""

    start: StartEffectAttempt = field(repr=False)
    context: TrustedCommandContext = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_health_command(self):
            raise InvalidOperationCommand("health effect start command is invalid")


@dataclass(frozen=True, slots=True)
class HealthEffectAttemptStartResult:
    """An original unsigned preparation with a new start or retained observation."""

    start: NewlyStarted | ExistingAttempt = field(repr=False)
    preparation: HealthEffectPreparationRecord = field(repr=False)

    def __post_init__(self) -> None:
        valid = False
        try:
            if (type(self) is HealthEffectAttemptStartResult
                    and type(self.start) in (NewlyStarted, ExistingAttempt)
                    and type(self.start.attempt) is EffectAttemptRecord
                    and type(self.preparation) is HealthEffectPreparationRecord):
                attempt = replace(self.start.attempt)
                start = type(self.start)(attempt)
                codec = HealthEffectPreparationCodec()
                preparation = codec.decode_canonical_bytes(
                    codec.encode_canonical_bytes(self.preparation))
                valid = (start == self.start and preparation == self.preparation
                    and preparation.identity == attempt.state.identity
                    and preparation.request_fingerprint == attempt.state.request_fingerprint
                    and preparation.original_event_id == attempt.original_start_event.event_id)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            pass
        if not valid:
            raise OperationsRecordError("health effect start result is invalid")


def _valid_health_command(command: object) -> bool:
    if type(command) is not StartHealthEffectAttempt:
        return False
    try:
        return (_valid_start_command(command.start)
            and type(command.start.intent.operation) is ObserveNodeHealth
            and _valid_context(command.context)
            and command.context.workspace_id == command.start.intent.source.workspace_id
            and re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", command.context.actor_id) is not None)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _valid_context(context: object) -> bool:
    # Reconstruct only after checking every nominal node and scalar. Equality
    # alone would admit str-enum lookalikes and forged principal/grant trees.
    if (type(context) is not TrustedCommandContext
            or type(context.principal) is not AuthenticatedPrincipal
            or type(context.workspace_id) is not str
            or not _scopes(context.granted_scopes)):
        return False
    principal = context.principal
    identity = principal.identity
    if (type(identity) is not PrincipalIdentity
            or type(identity.issuer) is not str
            or type(identity.subject_id) is not str
            or type(identity.kind) is not PrincipalKind
            or type(principal.workspace_grants) is not tuple):
        return False
    if any(type(grant) is not WorkspaceGrant
            or type(grant.workspace_id) is not str or not _scopes(grant.scopes)
            for grant in principal.workspace_grants):
        return False
    rebuilt = AuthenticatedPrincipal(
        PrincipalIdentity(identity.issuer, identity.subject_id, identity.kind),
        tuple(WorkspaceGrant(grant.workspace_id, grant.scopes)
            for grant in principal.workspace_grants),
    )
    return (rebuilt == principal and TrustedCommandContext(
        rebuilt, context.workspace_id, context.granted_scopes) == context)


def _scopes(value: object) -> bool:
    return type(value) is tuple and all(type(scope) is PolicyScope for scope in value)


__all__ = ["StartHealthEffectAttempt", "HealthEffectAttemptStartResult"]
