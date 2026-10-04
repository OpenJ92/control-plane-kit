"""Original fold admission, exact point proof and non-mutating verification."""
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCorrelation, configuration_invocation_completion_for_result,
)
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations._configuration_completion import _PreparedConfigurationCompletion
from control_plane_kit_operations.configuration_completion import ConfigurationInvocationCompletionRecord
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_evidence_capacity, _identity,
)
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, EffectAttemptOutcomeRecord
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _joined_read, _active_read, _Capacity, _Unavailable
from .configuration_preparation_store import _SELECT, _decode
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
        rows = read.bounded_rows(_TABLE, _COLUMNS, "run_id=%s AND activity_id=%s AND attempt=%s", _key(identity))
        if not rows:
            return None
        row = rows[0]
        refs = read.query(_SELECT + " WHERE r.run_id=%s AND r.activity_id=%s AND r.attempt=%s"
            " ORDER BY r.artifact_id LIMIT 33", _key(identity),
            records=33, octets=33 * 32768, cells=19, identities=2)
        if not 1 <= len(refs) <= 32:
            raise _Unavailable
        evidence = tuple(_decode(value, read) for value in refs)
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

    def _prepare(self, stores, guard, original, attempt, outcome):
        intent = original.intent
        if (intent.kind is not RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1
                or type(intent.operation) not in (StartNode, ReconcileNode)
                or original.identity != attempt.state.identity
                or original.original_start_event != attempt.original_start_event
                or original.request_fingerprint != attempt.state.request_fingerprint):
            raise OperationsRecordError(_ERROR)
        stores.graphs._require_receiver_lifecycle(guard, intent.source.workspace_id)
        if outcome is None:
            return None
        if (type(outcome) is not ExecutionEffectOutcome or outcome.identity != original.identity
                or outcome.request_fingerprint != original.request_fingerprint):
            raise OperationsRecordError(_ERROR)
        context = ConfigurationInvocationCorrelation(original.request_fingerprint,
            original.original_start_event.event_id, intent.kind, intent.source,
            intent.operation, intent.configuration_instances)
        completion = configuration_invocation_completion_for_result(context, outcome.result)
        if completion is None:
            return None
        read = _active_read(self._connection)
        if read is None or stores.connection is not self._connection or stores.configuration_completions is not self:
            raise OperationsRecordError(_ERROR)
        rows = read.bounded_rows(_TABLE, _COLUMNS, "run_id=%s AND activity_id=%s AND attempt=%s", _key(original.identity))
        if rows:
            raise OperationsRecordError(_ERROR)
        # Reserve the bounded terminal/result representation and write tail
        # before the fold allocates its normal IDs. No new IDs are minted here.
        future = ConfigurationEvidenceFootprint(8, 8192 + 16384 + 815, 64, 10)
        if configuration_evidence_capacity(read.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity
        prepared = _PreparedConfigurationCompletion(stores, guard, original, attempt, outcome, completion, read.accounting)
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
        record = _record(prepared.original.identity, outcome_record.workspace_id,
            prepared.original.request_fingerprint, prepared.completion, outcome_record.outcome, outcome_record.attempt)
        self._bound = outcome_record, record

    def _insert(self, prepared, outcome_record):
        read = self._require_issued(prepared)
        if self._bound is None or outcome_record is not self._bound[0]:
            raise OperationsRecordError(_ERROR)
        record = self._bound[1]
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
