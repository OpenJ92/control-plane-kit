"""Postgres representation for exact direct effect-attempt outcomes."""

from __future__ import annotations

import json

import rfc8785

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectResultKind,
    RunId,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
    SecretEndpointMaterial,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence,
    RuntimeEffectObservationFailure,
    RuntimeEffectObservedAbsent,
    RuntimeEffectObservedConflict,
    RuntimeEffectObservedFailed,
    RuntimeEffectObservedIndeterminate,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectObserverUnsupported,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectContractError,
    RuntimeEffectFailure,
    RuntimeEffectResult,
)
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    EffectOutcomeProfile,
    ExecutionEffectOutcome,
    ObservedEffectOutcome,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.observed_state import _observation_record
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import OperationsRecordError


_COLUMN_NAMES = (
    "run_id",
    "activity_id",
    "attempt",
    "workspace_id",
    "request_id",
    "profile",
    "preimage",
    "request_fingerprint",
    "fence_worker_id",
    "fence_generation",
    "status",
    "outcome_fingerprint",
    "prior_run_id",
    "prior_activity_id",
    "prior_attempt",
    "original_event_id",
    "original_event_run_id",
    "original_event_ordinal",
    "direct_event_id",
    "direct_event_run_id",
    "direct_event_ordinal",
    "observation_count",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)
_BOUNDED_COLUMNS = ", ".join(
    (
        "CASE WHEN octet_length(preimage) BETWEEN 1 AND 8192 "
        "THEN preimage ELSE NULL END AS preimage"
        if column == "preimage"
        else column
    )
    for column in _COLUMN_NAMES
)
_SELECT = f"SELECT {_BOUNDED_COLUMNS} FROM cpk_effect_attempt_outcomes"
_OBSERVATION_COLUMNS = (
    "observation_id",
    "workspace_id",
    "subject_id",
    "status",
    "observed_at",
    "evidence",
    "freshness",
    "graph_id",
    "probe_kind",
    "probe_outcome",
    "endpoint_context",
)
_OBSERVATION_SELECT = ", ".join(
    (
        "CASE WHEN octet_length(observation.evidence::text) BETWEEN 1 AND 8192 "
        "THEN observation.evidence ELSE NULL END AS evidence"
        if column == "evidence"
        else f"observation.{column}"
    )
    for column in _OBSERVATION_COLUMNS
)
_MEMBERSHIP_QUERY = f"""
SELECT membership.position, membership.observation_count,
       {_OBSERVATION_SELECT}
FROM cpk_effect_attempt_outcome_observations AS membership
JOIN cpk_observations AS observation
  ON observation.observation_id = membership.observation_id
 AND observation.workspace_id = membership.workspace_id
WHERE membership.run_id = %s
  AND membership.activity_id = %s
  AND membership.attempt = %s
ORDER BY membership.position
LIMIT %s
"""


class EffectAttemptOutcomeStore:
    """Caller-transactional immutable direct-outcome aggregate."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def insert(self, record: EffectAttemptOutcomeRecord) -> EffectAttemptOutcomeRecord:
        admitted = _require_record(record)
        identity = admitted.attempt.state.identity
        ownership = self._connection.execute(
            """
            SELECT run.request_id, request.workspace_id
            FROM cpk_activity_runs AS run
            JOIN cpk_execution_requests AS request
              ON request.request_id = run.request_id
            WHERE run.run_id = %s
            """,
            (identity.run_id.value,),
        ).fetchone()
        if ownership is None or ownership[1] != admitted.workspace_id:
            raise OperationsRecordError(
                "effect attempt outcome store input is invalid"
            ) from None
        request_id = ownership[0]
        self._connection.execute(
            f"""
            INSERT INTO cpk_effect_attempt_outcomes ({_COLUMNS})
            VALUES ({', '.join('%s' for _ in _COLUMN_NAMES)})
            """,
            _record_values(admitted, request_id),
        )
        count = len(admitted.endpoint_observations)
        for position, observation in enumerate(admitted.endpoint_observations):
            self._connection.execute(
                """
                INSERT INTO cpk_effect_attempt_outcome_observations
                  (run_id, activity_id, attempt, workspace_id,
                   observation_count, position, observation_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    identity.run_id.value,
                    identity.activity_id,
                    identity.attempt,
                    admitted.workspace_id,
                    count,
                    position,
                    observation.observation_id,
                ),
            )
        return record

    def get(
        self,
        identity: EffectAttemptIdentity,
        transition_event_id: str,
    ) -> EffectAttemptOutcomeRecord:
        admitted_identity = _require_identity(identity)
        _require_event_id(transition_event_id)
        row = self._connection.execute(
            f"""
            {_SELECT}
            WHERE run_id = %s
              AND activity_id = %s
              AND attempt = %s
              AND direct_event_id = %s
            """,
            (
                admitted_identity.run_id.value,
                admitted_identity.activity_id,
                admitted_identity.attempt,
                transition_event_id,
            ),
        ).fetchone()
        if row is None:
            raise KeyError("effect attempt outcome was not found")
        count = _observation_count(row)
        memberships = self._connection.execute(
            _MEMBERSHIP_QUERY,
            (row[0], row[1], row[2], count + 1),
        ).fetchall()
        return _decode_row(self._connection, row, memberships)


def _require_identity(value: object) -> EffectAttemptIdentity:
    if type(value) is not EffectAttemptIdentity:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    parts = None
    try:
        parts = (value.run_id.value, value.activity_id, value.attempt)
    except AttributeError:
        pass
    if parts is None:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    admitted = None
    try:
        admitted = EffectAttemptIdentity(RunId(parts[0]), parts[1], parts[2])
    except (TypeError, ValueError):
        pass
    if admitted is None:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    return admitted


def _require_event_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise OperationsRecordError(
            "effect attempt outcome store input is invalid"
        ) from None


def _require_record(value: object) -> EffectAttemptOutcomeRecord:
    if type(value) is not EffectAttemptOutcomeRecord:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    parts = None
    try:
        parts = (
            value.workspace_id,
            value.outcome,
            value.attempt,
            value.endpoint_observations,
        )
    except AttributeError:
        pass
    if parts is None:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    admitted = None
    failed = False
    try:
        admitted = EffectAttemptOutcomeRecord(*parts)
        preimage = _encode_preimage(admitted)
    except (TypeError, ValueError, RuntimeEffectContractError, OperationsRecordError):
        failed = True
        preimage = b""
    if failed or admitted is None or not 1 <= len(preimage) <= 8192:
        raise OperationsRecordError("effect attempt outcome store input is invalid")
    return admitted


def _encode_preimage(record: EffectAttemptOutcomeRecord) -> bytes:
    value = (
        record.outcome.result
        if type(record.outcome) is ExecutionEffectOutcome
        else record.outcome.observation
    )
    return rfc8785.dumps(value.descriptor())


def _record_values(
    record: EffectAttemptOutcomeRecord,
    request_id: str,
) -> tuple[object, ...]:
    state = record.attempt.state
    identity = state.identity
    prior = state.prior_attempt
    original = record.attempt.original_start_event
    direct = record.attempt.latest_transition_event
    return (
        identity.run_id.value,
        identity.activity_id,
        identity.attempt,
        record.workspace_id,
        request_id,
        record.outcome.profile.value,
        _encode_preimage(record),
        state.request_fingerprint,
        state.fence.worker_id,
        state.fence.generation,
        state.status.value,
        state.outcome_fingerprint,
        None if prior is None else prior.run_id.value,
        None if prior is None else prior.activity_id,
        None if prior is None else prior.attempt,
        original.event_id,
        original.run_id,
        original.ordinal,
        direct.event_id,
        direct.run_id,
        direct.ordinal,
        len(record.endpoint_observations),
    )


def _decode_row(
    connection: PostgresConnection,
    row: object,
    memberships: object,
) -> EffectAttemptOutcomeRecord:
    if type(row) not in (tuple, list) or len(row) != len(_COLUMN_NAMES):
        raise OperationsRecordError("effect attempt outcome row is invalid")
    failed = False
    try:
        preimage = _decode_preimage(row[6], row[5])
    except (
        KeyError,
        RecursionError,
        ValueError,
        RuntimeEffectContractError,
        OperationsRecordError,
    ):
        failed = True
        preimage = None
    if not failed:
        try:
            return _reconstruct_row(connection, row, preimage, memberships)
        except (KeyError, ValueError, RuntimeEffectContractError, OperationsRecordError):
            failed = True
    if failed:
        raise OperationsRecordError("effect attempt outcome row is invalid") from None
    raise RuntimeError("effect attempt outcome row decoder did not return")


def _reconstruct_row(
    connection: PostgresConnection,
    row: tuple[object, ...] | list[object],
    value: RuntimeEffectResult | object,
    memberships: object,
) -> EffectAttemptOutcomeRecord:
    identity = EffectAttemptIdentity(RunId(row[0]), row[1], row[2])
    prior = (
        None
        if row[12] is None and row[13] is None and row[14] is None
        else EffectAttemptIdentity(RunId(row[12]), row[13], row[14])
    )
    state = EffectAttemptState(
        identity=identity,
        request_fingerprint=row[7],
        fence=EffectAttemptFence(row[8], row[9]),
        status=EffectAttemptStatus(row[10]),
        outcome_fingerprint=row[11],
        prior_attempt=prior,
    )
    event_store = PostgresExecutionStore(connection)
    original = event_store.get_event(row[15])
    direct = event_store.get_event(row[18])
    if (
        (original.event_id, original.run_id, original.ordinal)
        != (row[15], row[16], row[17])
        or (direct.event_id, direct.run_id, direct.ordinal)
        != (row[18], row[19], row[20])
    ):
        raise ValueError("effect outcome event coordinate is invalid")
    attempt = EffectAttemptRecord(state, original, direct)
    profile = EffectOutcomeProfile(row[5])
    outcome = (
        ExecutionEffectOutcome(identity, row[7], value)
        if profile is EffectOutcomeProfile.EXECUTION_RESULT
        else ObservedEffectOutcome(identity, value)
    )
    observations = _membership_records(row, memberships)
    return EffectAttemptOutcomeRecord(row[3], outcome, attempt, observations)


def _observation_count(row: object) -> int:
    if type(row) not in (tuple, list) or len(row) != len(_COLUMN_NAMES):
        raise OperationsRecordError("effect attempt outcome row is invalid")
    count = row[21]
    if type(count) is not int or not 0 <= count <= 8192:
        raise OperationsRecordError("effect attempt outcome row is invalid")
    return count


def _membership_records(
    row: tuple[object, ...] | list[object],
    rows: object,
) -> tuple[object, ...]:
    count = _observation_count(row)
    if type(rows) not in (tuple, list) or len(rows) != count:
        raise ValueError("effect outcome observation membership is invalid")
    result = []
    for position, membership in enumerate(rows):
        if (
            type(membership) not in (tuple, list)
            or len(membership) != 2 + len(_OBSERVATION_COLUMNS)
            or membership[0] != position
            or membership[1] != count
        ):
            raise ValueError("effect outcome observation membership is invalid")
        result.append(_observation_record(tuple(membership[2:])))
    return tuple(result)


def _decode_preimage(value: object, profile: object) -> object:
    try:
        if type(value) is not bytes or not 1 <= len(value) <= 8192:
            raise ValueError("effect outcome preimage is invalid")
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if type(decoded) is not dict or rfc8785.dumps(decoded) != value:
            raise ValueError("effect outcome preimage is invalid")
        selected = EffectOutcomeProfile(profile)
        if selected is EffectOutcomeProfile.EXECUTION_RESULT:
            return _runtime_result(decoded)
        return _runtime_observation(decoded)
    except (KeyError, TypeError, UnicodeError, ValueError, RuntimeEffectContractError):
        raise ValueError("effect outcome preimage is invalid") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _exact_mapping(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("descriptor shape is invalid")
    return value


def _runtime_result(value: object) -> RuntimeEffectResult:
    row = _exact_mapping(
        value,
        {"effect_id", "kind", "evidence", "failure", "observations"},
    )
    evidence = _exact_mapping(row["evidence"], set(row["evidence"]))
    observations = _endpoints(row["observations"])
    failure = None if row["failure"] is None else _runtime_failure(row["failure"])
    return RuntimeEffectResult(
        row["effect_id"],
        EffectResultKind(row["kind"]),
        evidence,
        failure,
        observations,
    )


def _runtime_failure(value: object) -> RuntimeEffectFailure:
    row = _exact_mapping(value, {"code", "message", "details"})
    details = _exact_mapping(row["details"], set(row["details"]))
    return RuntimeEffectFailure(row["code"], row["message"], details)


def _runtime_observation(value: object) -> object:
    row = _exact_mapping(
        value,
        {
            "kind",
            "effect_id",
            "request_fingerprint",
            "evidence",
            "failure",
            "observations",
        },
    )
    evidence = RuntimeEffectObservationEvidence(
        _exact_mapping(row["evidence"], set(row["evidence"]))
    )
    failure = (
        None
        if row["failure"] is None
        else _runtime_observation_failure(row["failure"])
    )
    observations = _endpoints(row["observations"])
    variants = {
        "succeeded": RuntimeEffectObservedSucceeded,
        "failed": RuntimeEffectObservedFailed,
        "absent": RuntimeEffectObservedAbsent,
        "conflict": RuntimeEffectObservedConflict,
        "indeterminate": RuntimeEffectObservedIndeterminate,
        "observer-unsupported": RuntimeEffectObserverUnsupported,
    }
    constructor = variants[row["kind"]]
    return constructor(
        row["effect_id"],
        row["request_fingerprint"],
        evidence,
        failure,
        observations,
    )


def _runtime_observation_failure(value: object) -> RuntimeEffectObservationFailure:
    row = _exact_mapping(value, {"code", "message", "details"})
    details_row = _exact_mapping(row["details"], set(row["details"]))
    details = (
        None
        if not details_row
        else RuntimeEffectObservationEvidence(details_row)
    )
    return RuntimeEffectObservationFailure(row["code"], row["message"], details)


def _endpoints(value: object) -> tuple[RuntimeEndpointObservation, ...]:
    if type(value) is not list:
        raise ValueError("runtime endpoints are invalid")
    return tuple(_endpoint(item) for item in value)


def _endpoint(value: object) -> RuntimeEndpointObservation:
    row = _exact_mapping(
        value,
        {"subject_id", "socket_name", "graph_id", "protocol", "context", "address"},
    )
    protocol = Protocol.from_descriptor(
        _exact_mapping(row["protocol"], {"transport", "application"})
    )
    address_row = _exact_mapping(
        row["address"],
        set(row["address"]),
    )
    kind = address_row.get("kind")
    if kind == "literal" and set(address_row) == {"kind", "value"}:
        address = LiteralEndpointMaterial(address_row["value"])
    elif kind == "secret-reference" and set(address_row) == {"kind", "reference_id"}:
        address = SecretEndpointMaterial(address_row["reference_id"])
    else:
        raise ValueError("runtime endpoint address is invalid")
    return RuntimeEndpointObservation(
        row["subject_id"],
        row["socket_name"],
        row["graph_id"],
        protocol,
        EndpointContext(row["context"]),
        address,
    )


_FIRST_PAGE = f"""
SELECT {_BOUNDED_COLUMNS}
FROM cpk_effect_attempt_outcomes AS outcome
ORDER BY outcome.run_id, outcome.activity_id, outcome.attempt
LIMIT %s
"""
_NEXT_PAGE = f"""
SELECT {_BOUNDED_COLUMNS}
FROM cpk_effect_attempt_outcomes AS outcome
WHERE (outcome.run_id, outcome.activity_id, outcome.attempt) > (%s, %s, %s)
ORDER BY outcome.run_id, outcome.activity_id, outcome.attempt
LIMIT %s
"""


def _validate_current_rows(
    connection: PostgresConnection,
    *,
    limit: int = 64,
) -> None:
    after: tuple[object, object, object] | None = None
    while True:
        query = _FIRST_PAGE if after is None else _NEXT_PAGE
        parameters = (limit,) if after is None else (*after, limit)
        rows = connection.execute(query, parameters).fetchall()
        if type(rows) not in (tuple, list) or len(rows) > limit:
            raise OperationsRecordError("effect attempt outcome row is invalid")
        if not rows:
            return
        for row in rows:
            count = _observation_count(row)
            memberships = connection.execute(
                _MEMBERSHIP_QUERY,
                (row[0], row[1], row[2], count + 1),
            ).fetchall()
            _decode_row(connection, row, memberships)
        last = rows[-1]
        after = (last[0], last[1], last[2])
        if len(rows) < limit:
            return


__all__ = ["EffectAttemptOutcomeStore"]
