"""Single-dispatch custody records on the caller's PostgreSQL transaction."""

from psycopg import IntegrityError
from psycopg.types.json import Jsonb

from control_plane_kit_operations.secret_material_provision_attempts import (
    COMMAND_KIND, MAX_DESCRIPTOR_BYTES, ProvisionAttemptClaim, ProvisionAttemptConflict,
    ProvisionAttemptError, ProvisionAttemptIntent, ProvisionAttemptNotFound,
    ProvisionAttemptState, SecretMaterialProvisionAttempt,
    _identifier, decode_intent, decode_receipt, validated_receipt,
)
from .secret_provider_store import SecretReferenceStore
from .temporal import decode_postgres_timestamp, encode_postgres_timestamp


_TABLE = "cpk_secret_material_provision_attempts"
_SELECT = f"""SELECT workspace_id, command_kind, request_id, provider_registration_id,
    CASE WHEN octet_length(intent_descriptor::text) <= {MAX_DESCRIPTOR_BYTES}
         THEN intent_descriptor ELSE NULL END,
    intent_fingerprint, state,
    CASE WHEN octet_length(receipt_descriptor::text) <= {MAX_DESCRIPTOR_BYTES}
         THEN receipt_descriptor ELSE NULL END,
    reference_registration_id, created_at, updated_at FROM {_TABLE}"""


class SecretMaterialProvisionAttemptStore:
    def __init__(self, connection):
        self._connection = connection

    def reserve(self, intent):
        if type(intent) is not ProvisionAttemptIntent:
            raise ProvisionAttemptError("provision reservation requires safe typed intent")
        inserted = None
        try:
            inserted = self._connection.execute(
                f"INSERT INTO {_TABLE} (workspace_id,command_kind,request_id,provider_registration_id,"
                "intent_descriptor,intent_fingerprint,state,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'intended',%s,%s) "
                "ON CONFLICT (workspace_id,command_kind,request_id) DO NOTHING RETURNING request_id",
                (intent.workspace_id, COMMAND_KIND, intent.request_id, intent.grant.provider_registration_id,
                 Jsonb(intent.descriptor()), intent.fingerprint, encode_postgres_timestamp(intent.requested_at),
                 encode_postgres_timestamp(intent.requested_at)),
            ).fetchone()
        except IntegrityError:
            pass
        if inserted is None:
            raise ProvisionAttemptConflict("provision request exists or its durable references are unavailable")
        return self.get(intent.workspace_id, intent.request_id)

    def get(self, workspace_id, request_id):
        return self._get(workspace_id, request_id)

    def get_for_update(self, workspace_id, request_id):
        return self._get(workspace_id, request_id, lock=True)

    def _get(self, workspace_id, request_id, *, lock=False):
        _identifier(workspace_id)
        _identifier(request_id)
        row = self._connection.execute(
            _SELECT + " WHERE workspace_id=%s AND command_kind=%s AND request_id=%s"
            + (" FOR UPDATE" if lock else ""), (workspace_id, COMMAND_KIND, request_id),
        ).fetchone()
        if row is None:
            raise ProvisionAttemptNotFound("provision request is unavailable")
        result = _decode(row)
        if result.state is ProvisionAttemptState.ADMITTED:
            self._verify_admission(result)
        return result

    def require_claim(self, claim):
        if type(claim) is not ProvisionAttemptClaim:
            raise ProvisionAttemptError("provision claim is malformed")
        current = self._get(claim.workspace_id, claim.request_id, lock=True)
        if current.state is ProvisionAttemptState.INTENDED:
            raise ProvisionAttemptConflict("provision initial dispatch has not been claimed")
        return current

    def claim_initial(self, workspace_id, request_id, *, claimed_at):
        current = self._get(workspace_id, request_id, lock=True)
        _check_time(current, claimed_at)
        if current.state is not ProvisionAttemptState.INTENDED:
            return None
        self._connection.execute(
            f"UPDATE {_TABLE} SET state='dispatching',updated_at=%s "
            "WHERE workspace_id=%s AND command_kind=%s AND request_id=%s AND state='intended'",
            (encode_postgres_timestamp(claimed_at), workspace_id, COMMAND_KIND, request_id))
        return ProvisionAttemptClaim(workspace_id, request_id)

    def record_receipt(self, claim, receipt, *, observed_at):
        current = self.require_claim(claim)
        _check_time(current, observed_at)
        receipt = validated_receipt(current.intent, receipt)
        if current.state is ProvisionAttemptState.RECEIPT_RECORDED and current.receipt == receipt:
            return current
        if current.state is not ProvisionAttemptState.DISPATCHING:
            raise ProvisionAttemptConflict("provision receipt cannot replace recorded outcome")
        self._connection.execute(
            f"UPDATE {_TABLE} SET state='receipt-recorded',receipt_descriptor=%s,updated_at=%s "
            "WHERE workspace_id=%s AND command_kind=%s AND request_id=%s",
            (Jsonb(receipt.descriptor()), encode_postgres_timestamp(observed_at),
             claim.workspace_id, COMMAND_KIND, claim.request_id))
        return self.get(claim.workspace_id, claim.request_id)

    def record_uncertain(self, claim, *, observed_at):
        return self._outcome(claim, ProvisionAttemptState.UNCERTAIN, observed_at)

    def record_refusal(self, claim, *, observed_at):
        """Caller must know this initial dispatch has never invoked custody.

        This is an owner-internal transition, not a recovery command. Uncertain
        or receipt-bearing attempts cannot be relabeled as pre-invocation refusal.
        """
        return self._outcome(claim, ProvisionAttemptState.REFUSED, observed_at)

    def _outcome(self, claim, state, observed_at):
        current = self.require_claim(claim)
        _check_time(current, observed_at)
        if current.state is not ProvisionAttemptState.DISPATCHING:
            raise ProvisionAttemptConflict("provision outcome cannot replace recorded truth")
        self._connection.execute(
            f"UPDATE {_TABLE} SET state=%s,updated_at=%s "
            "WHERE workspace_id=%s AND command_kind=%s AND request_id=%s",
            (state.value, encode_postgres_timestamp(observed_at), claim.workspace_id, COMMAND_KIND, claim.request_id))
        return self.get(claim.workspace_id, claim.request_id)

    def record_admitted(self, claim, reference_registration_id, *, admitted_at):
        current = self.require_claim(claim)
        _check_time(current, admitted_at)
        _identifier(reference_registration_id)
        if current.state is not ProvisionAttemptState.RECEIPT_RECORDED:
            raise ProvisionAttemptConflict("provision has no pending admission")
        from dataclasses import replace
        candidate = replace(current, state=ProvisionAttemptState.ADMITTED,
                            reference_registration_id=reference_registration_id, updated_at=admitted_at)
        self._verify_admission(candidate)
        self._connection.execute(
            f"UPDATE {_TABLE} SET state='admitted',reference_registration_id=%s,updated_at=%s "
            "WHERE workspace_id=%s AND command_kind=%s AND request_id=%s",
            (reference_registration_id, encode_postgres_timestamp(admitted_at),
             claim.workspace_id, COMMAND_KIND, claim.request_id))
        return self.get(claim.workspace_id, claim.request_id)

    def _verify_admission(self, attempt):
        valid = False
        try:
            grant, receipt = attempt.intent.grant, attempt.receipt
            reference = SecretReferenceStore(self._connection).get_by_registration(
                attempt.intent.workspace_id, attempt.reference_registration_id)
            valid = (reference.reference == grant.reference
                     and reference.provider_registration_id == grant.provider_registration_id
                     and reference.allowed_intents == (grant.intent,)
                     and reference.admitted_by == attempt.intent.actor.subject_id
                     and reference.metadata.get("custody_id") == receipt.custody_id
                     and reference.metadata.get("provider_version_id") == receipt.version_id
                     and reference.metadata.get("provider_version_number") == receipt.version_number)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        if not valid:
            raise ProvisionAttemptConflict("provision admission does not match durable custody")


def _check_time(current, value):
    if encode_postgres_timestamp(value) < encode_postgres_timestamp(current.updated_at):
        raise ProvisionAttemptConflict("provision timestamp precedes recorded truth")


def _decode(row):
    result = None
    try:
        intent = decode_intent(row[4])
        if (row[0] != intent.workspace_id or row[1] != COMMAND_KIND or row[2] != intent.request_id
                or row[3] != intent.grant.provider_registration_id or row[5] != intent.fingerprint):
            raise ValueError
        result = SecretMaterialProvisionAttempt(
            intent, ProvisionAttemptState(row[6]), None if row[7] is None else decode_receipt(row[7]),
            row[8], decode_postgres_timestamp(row[9]), decode_postgres_timestamp(row[10]))
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    if result is None:
        raise ProvisionAttemptError("stored provision attempt is malformed")
    return result


def validate_current_rows(connection):
    cursor = ("", "")
    store = SecretMaterialProvisionAttemptStore(connection)
    while True:
        rows = connection.execute(
            _SELECT + " WHERE (workspace_id,request_id) > (%s,%s) "
            "ORDER BY workspace_id,request_id LIMIT 100", cursor,
        ).fetchall()
        for row in rows:
            attempt = _decode(row)
            if attempt.state is ProvisionAttemptState.ADMITTED:
                store._verify_admission(attempt)
        if len(rows) < 100:
            return
        cursor = (rows[-1][0], rows[-1][2])
