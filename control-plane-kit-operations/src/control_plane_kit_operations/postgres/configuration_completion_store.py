"""Original fold admission, exact point proof and non-mutating verification."""
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCorrelation, configuration_invocation_completion_for_result,
)
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.operations import EffectAttemptStatus, fold_effect_attempt
from control_plane_kit_core.operations.lifecycle import ExecutionRequestStatus, ActivityRunStatus
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.verification import VerificationCompleted
from control_plane_kit_operations._configuration_completion import _PreparedConfigurationCompletion
from control_plane_kit_operations.configuration_completion import ConfigurationInvocationCompletionRecord
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_evidence_capacity, _identity,
)
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, EffectAttemptOutcomeRecord, effect_outcome_transition
from control_plane_kit_operations.effect_run_prefix import PreparedEffectRunPrefix
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _joined_read, _active_read, _Capacity, _Unavailable
from .configuration_preparation_store import _SELECT, _decode, _paired_disposition
from .configuration_source import read_original_selection
from .effect_outcome_store import EffectAttemptOutcomeStore


_TABLE = "cpk_configuration_invocation_completions"
_COLUMNS = (("run_id", "text", 200), ("activity_id", "text", 200), ("attempt", "int", 10),
    ("workspace_id", "text", 128), ("request_fingerprint", "text", 64),
    ("selection_fingerprint", "text", 64), ("outcome_fingerprint", "text", 64))
_ERROR = "configuration completion is unavailable"


def _key(identity):
    _identity(identity)
    return identity.run_id.value, identity.activity_id, identity.attempt


def _record(identity, workspace, request, completion, outcome, attempt):
    return ConfigurationInvocationCompletionRecord(identity, workspace, request,
        completion.selection_fingerprint, attempt.state.outcome_fingerprint,
        attempt.original_start_event.event_id, attempt.original_start_event.ordinal,
        attempt.latest_transition_event.event_id, attempt.latest_transition_event.ordinal)


class ConfigurationCompletionStore:
    def __init__(self, connection):
        self._connection = connection
        self._issued = None
        self._bound = None

    def get(self, identity):
        result = None
        try:
            with _joined_read(self._connection) as read:
                result = self._get(identity, read)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        else:
            return result
        raise OperationsRecordError(_ERROR)

    def _get(self, identity, read):
        from .configuration_cleanup_phase_read_bounds import _phase_context
        _phase_context(self._connection, read=read)
        rows = read.bounded_rows(_TABLE, _COLUMNS, "run_id=%s AND activity_id=%s AND attempt=%s", _key(identity))
        if not rows:
            return None
        row = rows[0]
        from control_plane_kit_operations._configuration_acceptance import _observe_publication_proof
        _observe_publication_proof(read, "completion", identity, row)
        from .configuration_cleanup_phase_read_bounds import _phase_rows
        refs = _phase_rows(read, "invocation-refs", _key(identity))
        if refs is None:
            refs = read.query(_SELECT + " WHERE r.run_id=%s AND r.activity_id=%s AND r.attempt=%s"
            " ORDER BY r.artifact_id LIMIT 33", _key(identity),
            records=33, octets=33 * 32768, cells=19, identities=2)
        if not 1 <= len(refs) <= 32:
            raise _Unavailable
        _observe_publication_proof(read, "invocation", identity, tuple(refs))
        evidence = tuple(_decode(value, read) for value in refs)
        for value, claim in zip(refs, evidence, strict=True):
            _paired_disposition(read, tuple(value[:4]), claim.ref)
        source = evidence[0].source
        original = read_original_selection(self._connection, identity, evidence[0].ref, read=read)
        selection = ConfigurationInstanceSelection(tuple(value.ref for value in original))
        if tuple(value.ref for value in evidence) != selection.instances:
            raise _Unavailable
        outcome, attempt = EffectAttemptOutcomeStore(self._connection)._configuration_terminal(source, read)
        context = ConfigurationInvocationCorrelation(source.request_fingerprint, source.original_event_id,
            source.kind, source.source, source.operation, selection)
        completion = configuration_invocation_completion_for_result(context, outcome.result)
        if completion is None or row != (*_key(identity), source.source.workspace_id,
                source.request_fingerprint, completion.selection_fingerprint, attempt.state.outcome_fingerprint):
            raise _Unavailable
        return _record(identity, row[3], row[4], completion, outcome, attempt)

    def _prepare(self, stores, guard, original, attempt, outcome, *, unit_of_work=None,
                 prefix=None, request=None, fence=None):
        # Ordinary unprofiled/recovery behavior remains with its existing owner.
        # Only a present completion assertion enters D1's fresh-fold protocol.
        if type(outcome) is not ExecutionEffectOutcome:
            return None
        intent = original.intent
        context = ConfigurationInvocationCorrelation(original.request_fingerprint,
            original.original_start_event.event_id, intent.kind, intent.source,
            intent.operation, intent.configuration_instances)
        completion = configuration_invocation_completion_for_result(context, outcome.result)
        if completion is None:
            return None
        if (unit_of_work is None or unit_of_work.stores is not stores
                or stores.connection is not self._connection or stores.configuration_completions is not self
                or type(prefix) is not PreparedEffectRunPrefix or prefix.request != request
                or request is None or request.claim is None
                or request.status is not ExecutionRequestStatus.CLAIMED
                or fence is None or (request.claim.worker_id, request.claim.generation)
                    != (fence.worker_id, fence.generation)):
            raise OperationsRecordError(_ERROR)
        if (intent.kind is not RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1
                or type(intent.operation) not in (StartNode, ReconcileNode)
                or original.identity != attempt.state.identity
                or original.original_start_event != attempt.original_start_event
                or original.request_fingerprint != attempt.state.request_fingerprint):
            raise OperationsRecordError(_ERROR)
        stores.graphs._require_receiver_lifecycle(guard, intent.source.workspace_id)
        prefix.require(unit_of_work, request, attempt.state.identity.run_id.value, latest_required=True)
        if (prefix.latest_run != prefix.requested_run or prefix.requested_run.status is not ActivityRunStatus.RUNNING
                or request.identity.workspace_id != intent.source.workspace_id
                or request.identity.request_id != intent.source.request_id
                or stores.execution.get_request(request.identity.request_id) != request
                or stores.effect_attempt_intents.get(original.identity) != original
                or attempt.state.status is not EffectAttemptStatus.STARTED or attempt.state.fence != fence
                or stores.effect_attempts.get(original.identity) != attempt):
            raise OperationsRecordError(_ERROR)
        read = _active_read(self._connection)
        if read is None:
            raise OperationsRecordError(_ERROR)
        if read.query("SELECT 1 FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id=%s AND attempt=%s LIMIT 1",
                _key(original.identity), records=1, octets=1, cells=1):
            raise OperationsRecordError(_ERROR)
        if (outcome.identity != original.identity
                or outcome.request_fingerprint != original.request_fingerprint):
            raise OperationsRecordError(_ERROR)
        rows = read.bounded_rows(_TABLE, _COLUMNS, "run_id=%s AND activity_id=%s AND attempt=%s", _key(original.identity))
        if rows:
            raise OperationsRecordError(_ERROR)
        # Tracked tail: ordinal(2), bind/insert guard(2), event(1), CAS(1),
        # persisted four-owner correspondence(4), link(1). Value caps are the
        # existing SQL declarations: 21 + 40 + 1 + 200 + 1 + 1 bytes.
        future = ConfigurationEvidenceFootprint(11, 264, 8, 8)
        if any(type(value) is VerificationCompleted for value in outcome.endpoint_observations):
            # Generic membership re-reads the ten-column original intent and
            # three-column original event, each via length and guarded value
            # fetch. Include full supported caps, both validity Booleans and
            # every length cell; do not drop legitimate verification evidence.
            future = future.plus(ConfigurationEvidenceFootprint(4,
                1048576 + 9 * 2048 + 128 + 64 + 16384 + 13 * 12 + 2, 28, 4))
        # Existing generic outcome insertion fetches the already-proved
        # run/request ownership tuple outside _EvidenceRead. Charge its exact
        # known identity bytes and two owners. Its INSERT and each observation
        # plus membership INSERT return no rows but still consume statements.
        unmetered = ConfigurationEvidenceFootprint(2,
            len(request.identity.request_id.encode()) + len(intent.source.workspace_id.encode()),
            2, 2 + 2 * len(outcome.endpoint_observations))
        future = future.plus(unmetered)
        if configuration_evidence_capacity(read.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity
        # Reserve unmetered work once. Tracked SQL continues to charge its own
        # actual transport; failed commands retain their unused reservation.
        read.used = read.used.plus(unmetered)
        prepared = _PreparedConfigurationCompletion(stores, guard, original, attempt, outcome, completion,
            read.accounting, request, fence)
        self._issued, self._bound = prepared, None
        return prepared

    def _require_issued(self, prepared):
        if (type(prepared) is not _PreparedConfigurationCompletion or prepared is not self._issued
                or prepared.stores.connection is not self._connection
                or prepared.stores.configuration_completions is not self):
            raise OperationsRecordError(_ERROR)
        read = _active_read(self._connection)
        if read is None or read.accounting is not prepared.accounting:
            raise OperationsRecordError(_ERROR)
        prepared.stores.graphs._require_receiver_lifecycle(prepared.guard, prepared.original.intent.source.workspace_id)
        return read

    def _bind(self, prepared, outcome_record):
        self._require_issued(prepared)
        if (type(outcome_record) is not EffectAttemptOutcomeRecord
                or outcome_record.outcome != prepared.outcome
                or outcome_record.workspace_id != prepared.original.intent.source.workspace_id
                or outcome_record.attempt.state.identity != prepared.attempt.state.identity
                or outcome_record.attempt.original_start_event != prepared.attempt.original_start_event):
            raise OperationsRecordError(_ERROR)
        EffectAttemptOutcomeRecord.__post_init__(outcome_record)
        expected = fold_effect_attempt(prepared.attempt.state, effect_outcome_transition(prepared.outcome),
            fence=prepared.fence)
        if outcome_record.attempt.state != expected:
            raise OperationsRecordError(_ERROR)
        record = _record(prepared.original.identity, outcome_record.workspace_id,
            prepared.original.request_fingerprint, prepared.completion, outcome_record.outcome, outcome_record.attempt)
        self._bound = outcome_record, record

    def _insert(self, prepared, outcome_record):
        read = self._require_issued(prepared)
        if self._bound is None or outcome_record is not self._bound[0]:
            raise OperationsRecordError(_ERROR)
        record = self._bound[1]
        # Only the original fresh CAS and persisted direct outcome can consume
        # this preparation. These are bounded reads, never new row locks.
        terminal = outcome_record.attempt
        persisted = read.query("""
            SELECT 1 FROM cpk_effect_attempts a
            JOIN cpk_effect_attempt_outcomes o USING (run_id,activity_id,attempt)
            JOIN cpk_activity_runs r ON r.run_id=a.run_id
            JOIN cpk_execution_requests q ON q.request_id=r.request_id
            WHERE (a.run_id,a.activity_id,a.attempt)=(%s,%s,%s)
              AND a.request_fingerprint=%s AND a.outcome_fingerprint=%s AND a.status=%s
              AND a.fence_worker_id=%s AND a.fence_generation=%s
              AND a.original_event_id=%s AND a.original_event_ordinal=%s
              AND a.latest_event_id=%s AND a.latest_event_ordinal=%s
              AND o.workspace_id=%s AND o.request_fingerprint=a.request_fingerprint
              AND o.outcome_fingerprint=a.outcome_fingerprint
              AND o.original_event_id=a.original_event_id AND o.original_event_ordinal=a.original_event_ordinal
              AND o.direct_event_id=a.latest_event_id AND o.direct_event_ordinal=a.latest_event_ordinal
              AND q.request_id=%s AND q.workspace_id=o.workspace_id AND q.status='claimed'
              AND q.claim_worker_id=a.fence_worker_id AND q.claim_generation=a.fence_generation
              AND r.status='running'
            LIMIT 1
            """, (*_key(record.identity), record.request_fingerprint, record.outcome_fingerprint,
                terminal.state.status.value, prepared.fence.worker_id, prepared.fence.generation,
                record.original_event_id, record.original_event_ordinal, record.direct_event_id,
                record.direct_event_ordinal, record.workspace_id, prepared.request.identity.request_id),
            records=1, octets=1, cells=1, identities=4)
        if persisted != [(1,)]:
            raise OperationsRecordError(_ERROR)
        values = (*_key(record.identity), record.workspace_id, record.request_fingerprint,
            record.selection_fingerprint, record.outcome_fingerprint)
        rows = read.query("INSERT INTO " + _TABLE + " (" + ",".join(column[0] for column in _COLUMNS)
            + ") VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING 1", values, records=1, octets=1, cells=1)
        if rows != [(1,)]:
            raise OperationsRecordError(_ERROR)
        self._issued, self._bound = None, None
        return record


def validate_configuration_completion_rows(connection):
    from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
    cursor = ("", "", 0)
    while True:
        rows = connection.execute("SELECT run_id,activity_id,attempt FROM " + _TABLE
            + " WHERE (run_id,activity_id,attempt)>(%s,%s,%s) ORDER BY run_id,activity_id,attempt LIMIT 32", cursor).fetchall()
        if not rows:
            return
        for row in rows:
            if ConfigurationCompletionStore(connection).get(EffectAttemptIdentity(RunId(row[0]), row[1], row[2])) is None:
                raise OperationsRecordError(_ERROR)
        cursor = rows[-1]
