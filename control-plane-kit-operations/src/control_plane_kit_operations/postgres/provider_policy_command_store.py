"""Postgres owner for provider-policy commands and append-only observations."""

from __future__ import annotations

from dataclasses import replace
from psycopg.types.json import Jsonb

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.provider_policy import (
    ProviderPolicyCommandResult,
    ProviderPolicyCoordinate,
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyGrantCommand,
    ProviderPolicyOperation,
    ProviderPolicyOutcome,
    ProviderPolicyRevokeCommand,
)
from control_plane_kit_operations.provider_policy_commands import (
    ProviderPolicyBinding,
    ProviderPolicyCommandConflict,
    ProviderPolicyCommandError,
    ProviderPolicyCommandNotFound,
    ProviderPolicyCommandRecord,
    ProviderPolicyObservationSource,
    ProviderPolicyOutcomeObservation,
)
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.workflows import IdempotencyKey


_COMMAND_COLUMNS = (
    "command_id,installation_id,caller_issuer,caller_subject_id,caller_kind,"
    "logical_provider_id,binding_id,provider_id,idempotency_key,request_fingerprint,"
    "provider_correlation,operation,intent_descriptor,dispatch_claimed_at,created_at"
)
_OBSERVATION_PAGE_SIZE = 100


class ProviderPolicyCommandStore:
    def __init__(self, connection) -> None:
        self.connection = connection

    def reserve(self, candidate: ProviderPolicyCommandRecord) -> ProviderPolicyCommandRecord:
        if type(candidate) is not ProviderPolicyCommandRecord or candidate.dispatch_claimed_at is not None:
            raise ProviderPolicyCommandError("provider-policy reservation is malformed")
        self.connection.execute(
            "INSERT INTO cpk_provider_policy_commands (" + _COMMAND_COLUMNS + ") "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s) "
            "ON CONFLICT (installation_id,caller_issuer,caller_subject_id,caller_kind,"
            "logical_provider_id,idempotency_key) DO NOTHING",
            (
                candidate.command_id,
                candidate.installation_id,
                candidate.actor.issuer,
                candidate.actor.subject_id,
                candidate.actor.kind.value,
                candidate.binding.logical_provider_id,
                candidate.binding.binding_id,
                candidate.binding.provider_id,
                candidate.idempotency_key.value,
                candidate.request_fingerprint,
                candidate.provider_correlation,
                candidate.command.operation.value,
                Jsonb(_command_descriptor(candidate.command)),
                encode_postgres_timestamp(candidate.created_at),
            ),
        )
        current = self.get_for_key(
            candidate.installation_id,
            candidate.actor,
            candidate.binding.logical_provider_id,
            candidate.idempotency_key.value,
        )
        if (
            current.command_id != candidate.command_id
            or current.request_fingerprint != candidate.request_fingerprint
            or current.binding != candidate.binding
        ):
            raise ProviderPolicyCommandConflict("provider-policy idempotency key conflicts")
        return current

    def claim(self, command_id: str, *, claimed_at: str) -> ProviderPolicyCommandRecord | None:
        result = self.connection.execute(
            "UPDATE cpk_provider_policy_commands SET dispatch_claimed_at=%s "
            "WHERE command_id=%s AND dispatch_claimed_at IS NULL RETURNING command_id",
            (encode_postgres_timestamp(claimed_at), command_id),
        ).fetchone()
        return None if result is None else self.get(command_id)

    def append_observation(
        self, command_id: str, observation: ProviderPolicyOutcomeObservation
    ) -> ProviderPolicyCommandRecord:
        if type(observation) is not ProviderPolicyOutcomeObservation:
            raise ProviderPolicyCommandError("provider-policy observation is malformed")
        command = self.get(command_id)
        if command.dispatch_claimed_at is None:
            raise ProviderPolicyCommandConflict("provider-policy command is not claimed")
        _validate_observation(command, observation)
        self.connection.execute(
            "INSERT INTO cpk_provider_policy_outcome_observations "
            "(observation_id,command_id,source,evidence_fingerprint,descriptor,observed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (command_id,evidence_fingerprint) DO NOTHING",
            (
                observation.observation_id,
                command_id,
                observation.source.value,
                observation.evidence_fingerprint,
                Jsonb(_observation_descriptor(observation)),
                encode_postgres_timestamp(observation.observed_at),
            ),
        )
        return self.get(command_id)

    def get(self, command_id: str) -> ProviderPolicyCommandRecord:
        row = self.connection.execute(
            "SELECT " + _COMMAND_COLUMNS + " FROM cpk_provider_policy_commands WHERE command_id=%s",
            (command_id,),
        ).fetchone()
        if row is None:
            raise ProviderPolicyCommandNotFound("provider-policy command is missing")
        return self._decode(row)

    def get_for_key(
        self,
        installation_id: str,
        actor: PrincipalIdentity,
        logical_provider_id: str,
        idempotency_key: str,
    ) -> ProviderPolicyCommandRecord:
        row = self.connection.execute(
            "SELECT " + _COMMAND_COLUMNS + " FROM cpk_provider_policy_commands "
            "WHERE installation_id=%s AND caller_issuer=%s AND caller_subject_id=%s "
            "AND caller_kind=%s AND logical_provider_id=%s AND idempotency_key=%s",
            (
                installation_id,
                actor.issuer,
                actor.subject_id,
                actor.kind.value,
                logical_provider_id,
                idempotency_key,
            ),
        ).fetchone()
        if row is None:
            raise ProviderPolicyCommandNotFound("provider-policy command is missing")
        return self._decode(row)

    def _decode(self, row) -> ProviderPolicyCommandRecord:
        try:
            actor = PrincipalIdentity(row[2], row[3], PrincipalKind(row[4]))
            binding = ProviderPolicyBinding(row[6], row[5], row[7])
            command = _decode_command(
                row[11], row[5], row[7], row[8], row[10], row[12]
            )
            record = ProviderPolicyCommandRecord(
                command_id=row[0],
                installation_id=row[1],
                actor=actor,
                binding=binding,
                idempotency_key=IdempotencyKey(row[8]),
                request_fingerprint=row[9],
                provider_correlation=row[10],
                command=command,
                created_at=decode_postgres_timestamp(row[14]),
                dispatch_claimed_at=(
                    None if row[13] is None else decode_postgres_timestamp(row[13])
                ),
            )
            return self._with_observations(record)
        except (KeyError, TypeError, ValueError, IndexError):
            raise ProviderPolicyCommandError(
                "persisted provider-policy command is malformed"
            ) from None

    def _with_observations(self, record):
        projected = []
        canonical = set()
        count = 0
        has_match = False
        has_refusal = False
        conflict = False
        while True:
            rows = self.connection.execute(
                "SELECT observation_id,source,evidence_fingerprint,descriptor,observed_at "
                "FROM cpk_provider_policy_outcome_observations WHERE command_id=%s "
                "ORDER BY observed_at,observation_id LIMIT %s OFFSET %s",
                (record.command_id, _OBSERVATION_PAGE_SIZE, count),
            ).fetchall()
            for row in rows:
                observation = _decode_observation(row)
                matches, refusal = _validate_observation(record, observation)
                if len(projected) < _OBSERVATION_PAGE_SIZE:
                    projected.append(observation)
                count += 1
                if observation.canonical_result is not None:
                    canonical.add(observation.evidence_fingerprint)
                    conflict |= not matches
                has_match |= matches
                has_refusal |= refusal
            if len(rows) < _OBSERVATION_PAGE_SIZE:
                break
        conflict |= len(canonical) > 1 or (has_match and has_refusal)
        return replace(
            record,
            observations=tuple(projected),
            observation_count=count,
            evidence_integrity_conflict=conflict,
            has_matching_result=has_match,
            has_definite_refusal=has_refusal,
        )


def _command_descriptor(command):
    descriptor = {
        "subject": command.coordinate.subject,
        "workspace_id": command.coordinate.workspace_id,
        "action": command.coordinate.action,
        "intent": command.coordinate.intent,
        "expected_revision": command.expected_revision,
    }
    if type(command) is ProviderPolicyRevokeCommand:
        descriptor["grant_id"] = command.grant_id
    return descriptor


def _decode_command(operation, logical_provider_id, provider_id, key, correlation, value):
    if type(value) is not dict:
        raise ValueError
    expected = {"subject", "workspace_id", "action", "intent", "expected_revision"}
    if operation == ProviderPolicyOperation.REVOKE.value:
        expected.add("grant_id")
    if set(value) != expected:
        raise ValueError
    common = dict(
        logical_provider_id=logical_provider_id,
        provider_id=provider_id,
        coordinate=ProviderPolicyCoordinate(
            value["subject"], value["workspace_id"], value["action"], value["intent"]
        ),
        expected_revision=value["expected_revision"],
        caller_idempotency_key=key,
        provider_correlation=correlation,
    )
    if operation == ProviderPolicyOperation.GRANT.value:
        return ProviderPolicyGrantCommand(**common)
    if operation == ProviderPolicyOperation.REVOKE.value:
        return ProviderPolicyRevokeCommand(**common, grant_id=value["grant_id"])
    raise ValueError


def _observation_descriptor(value):
    if value.canonical_result is not None:
        result = value.canonical_result
        evidence = {
            "kind": "canonical-result",
            "provider_id": result.provider_id,
            "correlation_id": result.correlation_id,
            "operation": result.operation.value,
            **result.coordinate.descriptor(),
            "expected_revision": result.expected_revision,
            "outcome": result.outcome.value,
            "code": result.code,
            "grant_id": result.grant_id,
            "resulting_revision": result.resulting_revision,
            "provider_actor": result.provider_actor,
            "occurred_at": result.occurred_at,
            "matches_intent": value.matches_intent,
        }
    else:
        evidence = {
            "kind": "failure",
            **value.failure.descriptor(),
            "matches_intent": False,
        }
    return evidence


def _decode_observation(row):
    value = row[3]
    if type(value) is not dict:
        raise ValueError
    kind = value.get("kind")
    if kind == "canonical-result":
        expected = {
            "kind", "provider_id", "correlation_id", "operation", "subject",
            "workspace_id", "action", "intent", "expected_revision", "outcome",
            "code", "grant_id", "resulting_revision", "provider_actor", "occurred_at",
            "matches_intent",
        }
        if set(value) != expected:
            raise ValueError
        result = ProviderPolicyCommandResult(
            provider_id=value["provider_id"],
            correlation_id=value["correlation_id"],
            operation=ProviderPolicyOperation(value["operation"]),
            coordinate=ProviderPolicyCoordinate(
                value["subject"], value["workspace_id"], value["action"], value["intent"]
            ),
            expected_revision=value["expected_revision"],
            outcome=ProviderPolicyOutcome(value["outcome"]),
            code=value["code"],
            grant_id=value["grant_id"],
            resulting_revision=value["resulting_revision"],
            provider_actor=value["provider_actor"],
            occurred_at=value["occurred_at"],
        )
        failure = None
        matches = value["matches_intent"]
    elif kind == "failure":
        if set(value) != {"kind", "code", "message", "effect_may_have_occurred", "matches_intent"}:
            raise ValueError
        result = None
        failure = ProviderPolicyFailure(
            ProviderPolicyErrorCode(value["code"]),
            value["message"],
            effect_may_have_occurred=value["effect_may_have_occurred"],
        )
        matches = False
    else:
        raise ValueError
    return ProviderPolicyOutcomeObservation(
        observation_id=row[0],
        source=ProviderPolicyObservationSource(row[1]),
        evidence_fingerprint=row[2],
        canonical_result=result,
        failure=failure,
        matches_intent=matches,
        observed_at=decode_postgres_timestamp(row[4]),
    )


def _validate_observation(command, observation):
    matches = (
        observation.canonical_result is not None
        and observation.canonical_result.matches(command.command)
    )
    refusal = (
        observation.failure is not None
        and observation.source is ProviderPolicyObservationSource.INITIAL_RESPONSE
        and observation.failure.code is not ProviderPolicyErrorCode.CONFLICT
        and not observation.failure.effect_may_have_occurred
    )
    replace(
        command,
        observations=(observation,),
        observation_count=1,
        evidence_integrity_conflict=(
            observation.canonical_result is not None and not matches
        ),
        has_matching_result=matches,
        has_definite_refusal=refusal,
    )
    return matches, refusal


def validate_provider_policy_command_rows(connection) -> None:
    store = ProviderPolicyCommandStore(connection)
    cursor = ""
    while True:
        rows = connection.execute(
            "SELECT " + _COMMAND_COLUMNS + " FROM cpk_provider_policy_commands "
            "WHERE command_id > %s ORDER BY command_id LIMIT 100",
            (cursor,),
        ).fetchall()
        for row in rows:
            store._decode(row)
        if len(rows) < 100:
            return
        cursor = rows[-1][0]


__all__ = ["ProviderPolicyCommandStore", "validate_provider_policy_command_rows"]
