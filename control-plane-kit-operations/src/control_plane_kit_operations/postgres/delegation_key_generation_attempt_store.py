"""Exact generation-attempt persistence on the caller's PostgreSQL connection."""

from __future__ import annotations

from dataclasses import replace

from psycopg import IntegrityError
from psycopg.types.json import Jsonb

from control_plane_kit_operations.delegation_key_generation import AdmittedGeneratedDelegationSigningKey
from control_plane_kit_operations.delegation_key_generation_attempts import (
    COMMAND_KIND, MAX_DESCRIPTOR_BYTES, MAX_FENCE,
    DelegationKeyGenerationAttempt, GenerationAttemptClaim, GenerationAttemptConflict,
    GenerationAttemptError, GenerationAttemptIntent, GenerationAttemptState, GenerationClaimKind,
    _identifier, decode_intent, decode_receipt, receipt_descriptor, validated_receipt,
)
from .schema import PostgresConnection
from .temporal import decode_postgres_timestamp, encode_postgres_timestamp


_TABLE = "cpk_delegation_key_generation_attempts"
_COLUMNS = (
    "workspace_id, command_kind, request_id, intent_descriptor, intent_fingerprint, "
    "state, fence, claim_kind, receipt_descriptor, reference_registration_id, "
    "key_registration_id, created_at, updated_at"
)
_SELECT = f"""SELECT workspace_id, command_kind, request_id,
    CASE WHEN octet_length(intent_descriptor::text) <= {MAX_DESCRIPTOR_BYTES}
         THEN intent_descriptor ELSE NULL END,
    intent_fingerprint, state, fence, claim_kind,
    CASE WHEN octet_length(receipt_descriptor::text) <= {MAX_DESCRIPTOR_BYTES}
         THEN receipt_descriptor ELSE NULL END,
    reference_registration_id, key_registration_id, created_at, updated_at
    FROM {_TABLE}"""


class DelegationKeyGenerationAttemptStore:
    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def reserve(self, intent: GenerationAttemptIntent, *, created_at: str) -> DelegationKeyGenerationAttempt:
        if type(intent) is not GenerationAttemptIntent:
            raise GenerationAttemptError("generation attempt intent is malformed")
        timestamp = encode_postgres_timestamp(created_at)
        invalid = False
        try:
            self._connection.execute(
                f"INSERT INTO {_TABLE} ({_COLUMNS}) VALUES "
                "(%s,%s,%s,%s,%s,'intended',0,NULL,NULL,NULL,NULL,%s,%s) "
                "ON CONFLICT (workspace_id,command_kind,request_id) DO NOTHING",
                (intent.workspace_id, COMMAND_KIND, intent.request_id, Jsonb(intent.descriptor()),
                 intent.fingerprint, timestamp, timestamp),
            )
        except IntegrityError:
            invalid = True
        if invalid:
            raise GenerationAttemptConflict("generation attempt references are unavailable")
        current = self._get(intent.workspace_id, intent.request_id, lock=True)
        if current.intent.fingerprint != intent.fingerprint:
            raise GenerationAttemptConflict("generation request conflicts with recorded intent")
        return current

    def get(self, workspace_id: str, request_id: str) -> DelegationKeyGenerationAttempt:
        return self._get(workspace_id, request_id)

    def _get(self, workspace_id: str, request_id: str, *, lock=False) -> DelegationKeyGenerationAttempt:
        _identifier(workspace_id)
        _identifier(request_id)
        row = self._connection.execute(
            _SELECT + " WHERE workspace_id=%s AND command_kind=%s AND request_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (workspace_id, COMMAND_KIND, request_id),
        ).fetchone()
        if row is None:
            raise GenerationAttemptConflict("generation attempt is unavailable")
        result = _decode(row)
        if result.state is GenerationAttemptState.ADMITTED:
            self._verify_admission(result)
        return result

    def require_claim(self, claim: GenerationAttemptClaim) -> DelegationKeyGenerationAttempt:
        if type(claim) is not GenerationAttemptClaim:
            raise GenerationAttemptError("generation attempt claim is malformed")
        current = self._get(claim.workspace_id, claim.request_id, lock=True)
        if current.fence != claim.fence or current.claim_kind is not claim.kind:
            raise GenerationAttemptConflict("generation attempt claim is stale")
        return current

    def claim_initial(self, workspace_id: str, request_id: str, *, expected_fence: int,
                      claimed_at: str) -> GenerationAttemptClaim | None:
        return self._claim(workspace_id, request_id, expected_fence, claimed_at, reconcile=False)

    def claim_reconciliation(self, workspace_id: str, request_id: str, *, expected_fence: int,
                             claimed_at: str) -> GenerationAttemptClaim | None:
        return self._claim(workspace_id, request_id, expected_fence, claimed_at, reconcile=True)

    def _claim(self, workspace_id, request_id, expected_fence, claimed_at, *, reconcile):
        if type(expected_fence) is not int or not 0 <= expected_fence < MAX_FENCE:
            raise GenerationAttemptError("generation attempt fence is malformed")
        timestamp = encode_postgres_timestamp(claimed_at)
        current = self._get(workspace_id, request_id, lock=True)
        if timestamp < encode_postgres_timestamp(current.updated_at):
            raise GenerationAttemptConflict("generation attempt timestamp precedes recorded truth")
        permitted = (GenerationAttemptState.INTENDED,)
        if reconcile:
            permitted += (GenerationAttemptState.DISPATCHING, GenerationAttemptState.UNCERTAIN,
                          GenerationAttemptState.RECEIPT_RECORDED)
        if current.fence != expected_fence or current.state not in permitted:
            return None
        kind = (GenerationClaimKind.RECONCILE_ADMISSION if current.receipt is not None
                else GenerationClaimKind.RECONCILE_PROVIDER if reconcile else GenerationClaimKind.INITIAL)
        state = (GenerationAttemptState.RECEIPT_RECORDED if current.receipt is not None
                 else GenerationAttemptState.DISPATCHING)
        self._connection.execute(
            f"UPDATE {_TABLE} SET state=%s,fence=fence+1,claim_kind=%s,updated_at=%s "
            "WHERE workspace_id=%s AND command_kind=%s AND request_id=%s AND fence=%s",
            (state.value, kind.value, timestamp, workspace_id, COMMAND_KIND, request_id, expected_fence),
        )
        return GenerationAttemptClaim(workspace_id, request_id, expected_fence + 1, kind)

    def record_receipt(self, claim: GenerationAttemptClaim, evidence: object, *, observed_at: str):
        current = self.require_claim(claim)
        _check_time(current, observed_at)
        evidence = validated_receipt(current.intent, evidence)
        if current.state is GenerationAttemptState.RECEIPT_RECORDED:
            if replace(evidence, replayed=current.receipt.replayed) == current.receipt:
                return current
            raise GenerationAttemptConflict("generation receipt identity cannot change")
        if current.state not in (GenerationAttemptState.DISPATCHING, GenerationAttemptState.UNCERTAIN):
            raise GenerationAttemptConflict("generation attempt cannot receive a receipt")
        self._update(claim, GenerationAttemptState.RECEIPT_RECORDED, observed_at,
                     receipt=Jsonb(receipt_descriptor(evidence)))
        return self.get(claim.workspace_id, claim.request_id)

    def record_uncertain(self, claim, *, observed_at):
        return self._outcome(claim, GenerationAttemptState.UNCERTAIN, observed_at)

    def record_refusal(self, claim, *, observed_at):
        return self._outcome(claim, GenerationAttemptState.REFUSED, observed_at)

    def record_definite_failure(self, claim, *, observed_at):
        return self._outcome(claim, GenerationAttemptState.FAILED_DEFINITE, observed_at)

    def _outcome(self, claim, state, observed_at):
        current = self.require_claim(claim)
        _check_time(current, observed_at)
        if current.state not in (GenerationAttemptState.DISPATCHING, GenerationAttemptState.UNCERTAIN):
            raise GenerationAttemptConflict("generation outcome cannot replace durable truth")
        self._update(claim, state, observed_at)
        return self.get(claim.workspace_id, claim.request_id)

    def record_admitted(self, claim, admitted, *, admitted_at):
        current = self.require_claim(claim)
        _check_time(current, admitted_at)
        if current.state is not GenerationAttemptState.RECEIPT_RECORDED:
            raise GenerationAttemptConflict("generation attempt has no pending admission")
        if type(admitted) is not AdmittedGeneratedDelegationSigningKey:
            raise GenerationAttemptError("generation admission result is malformed")
        candidate = replace(
            current, state=GenerationAttemptState.ADMITTED,
            reference_registration_id=admitted.secret_reference.registration_id,
            key_registration_id=admitted.signing_key.registration_id, updated_at=admitted_at,
        )
        if (admitted.provider_version_id != current.receipt.version_id
                or admitted.provider_version_number != current.receipt.version_number):
            raise GenerationAttemptConflict("generation admission does not match receipt")
        self._verify_admission(candidate)
        self._connection.execute(
            f"UPDATE {_TABLE} SET state='admitted',reference_registration_id=%s,"
            "key_registration_id=%s,updated_at=%s WHERE workspace_id=%s "
            "AND command_kind=%s AND request_id=%s AND fence=%s AND state='receipt-recorded'",
            (candidate.reference_registration_id, candidate.key_registration_id,
             encode_postgres_timestamp(admitted_at), claim.workspace_id, COMMAND_KIND,
             claim.request_id, claim.fence),
        )
        return self.get(claim.workspace_id, claim.request_id)

    def _update(self, claim, state, timestamp, *, receipt=None):
        self._connection.execute(
            f"UPDATE {_TABLE} SET state=%s,updated_at=%s,"
            "receipt_descriptor=COALESCE(%s,receipt_descriptor) WHERE workspace_id=%s "
            "AND command_kind=%s AND request_id=%s AND fence=%s",
            (state.value, encode_postgres_timestamp(timestamp), receipt, claim.workspace_id,
             COMMAND_KIND, claim.request_id, claim.fence),
        )

    def _verify_admission(self, attempt):
        # Historical admission remains readable after subsequent lifecycle changes.
        from .secret_provider_store import SecretReferenceStore
        from .delegation_signing_key_store import DelegationSigningKeyStore
        invalid = False
        try:
            intent, evidence = attempt.intent, attempt.receipt
            reference = SecretReferenceStore(self._connection).get_by_registration(
                intent.workspace_id, attempt.reference_registration_id,
            )
            key = DelegationSigningKeyStore(self._connection).get(
                intent.workspace_id, intent.grant.purpose, intent.grant.issuer, evidence.public_key.key_id,
            )
            valid = (
                reference.reference == intent.grant.reference
                and reference.provider_registration_id == intent.grant.provider_registration_id
                and reference.allowed_intents == (intent.grant.custody_grant.intent,)
                and reference.metadata.get("provider_version_id") == evidence.version_id
                and reference.metadata.get("provider_version_number") == evidence.version_number
                and key.registration_id == attempt.key_registration_id
                and key.private_key_reference == intent.grant.reference
                and key.public_key == evidence.public_key
            )
            invalid = not valid
        except (ValueError, KeyError, TypeError, AttributeError):
            invalid = True
        if invalid:
            raise GenerationAttemptConflict("generation admission does not match durable identity")


def _check_time(current, value):
    if encode_postgres_timestamp(value) < encode_postgres_timestamp(current.updated_at):
        raise GenerationAttemptConflict("generation attempt timestamp precedes recorded truth")


def _decode(row) -> DelegationKeyGenerationAttempt:
    failed = False
    try:
        intent = decode_intent(row[3])
        if (row[0] != intent.workspace_id or row[1] != COMMAND_KIND
                or row[2] != intent.request_id or row[4] != intent.fingerprint):
            raise ValueError
        result = DelegationKeyGenerationAttempt(
            intent, GenerationAttemptState(row[5]), row[6],
            None if row[7] is None else GenerationClaimKind(row[7]),
            None if row[8] is None else decode_receipt(row[8]), row[9], row[10],
            decode_postgres_timestamp(row[11]), decode_postgres_timestamp(row[12]),
        )
    except (ValueError, TypeError, KeyError, IndexError):
        failed = True
    if failed:
        raise GenerationAttemptError("stored generation attempt is malformed")
    return result


def validate_current_rows(connection: PostgresConnection) -> None:
    cursor = ("", "")
    store = DelegationKeyGenerationAttemptStore(connection)
    while True:
        rows = connection.execute(
            _SELECT + " WHERE (workspace_id,request_id) > (%s,%s) "
            "ORDER BY workspace_id,request_id LIMIT 100", cursor,
        ).fetchall()
        for row in rows:
            value = _decode(row)
            if value.state is GenerationAttemptState.ADMITTED:
                store._verify_admission(value)
        if len(rows) < 100:
            return
        cursor = (rows[-1][0], rows[-1][2])
