"""Prepared original advancement values; no independent mutation authority."""
from dataclasses import dataclass, replace
from contextvars import ContextVar

from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp


_PUBLICATION_SCOPE = ContextVar("cpk_configuration_publication_scope", default=None)


@dataclass(frozen=True, repr=False)
class _PublicationReadBounds:
    """Immutable transport data carried by the existing prepared owner value."""
    proof_keys: tuple
    transaction_id: int = 0
    points: tuple = ()
    collections: tuple = ()
    optional: tuple = ()
    candidates: tuple = ()
    published: tuple = ()
    proof_shapes: tuple = ()
    receiver_receipts: tuple = ()
    transfer_dependencies: tuple = ()
    invocation_members: tuple = ()
    generated_widths: tuple = ()


@dataclass(frozen=True, repr=False)
class _ProspectiveConfigurationTransfer:
    """Expected own transfer and proof closure; never a proved transfer memo."""
    identity: object
    ref: object
    completion: object
    slot: tuple
    row: tuple
    memo: tuple
    refs: tuple
    sources: tuple


def _require_publication_proof(read, family, key):
    owner = _PUBLICATION_SCOPE.get()
    if owner is not None:
        owner._require_proof_cache(read, family, key)


def _observe_publication_proof(read, family, key, row):
    owner = _PUBLICATION_SCOPE.get()
    if owner is not None:
        owner._observe_proof_shape(read, family, key, row)


def _history_records(event, action):
    evidence = event.evidence.descriptor()
    if (event.activity_id is not None or event.failure is not None or event.recovery is not None
            or set(action.payload) != set(evidence) | {"execution_request_id", "claim_generation", "event_id"}
            or type(action.payload.get("claim_generation")) is not int
            or action.payload["claim_generation"] < 1
            or action.idempotency_key is None or action.intent_fingerprint is None):
        raise OperationsRecordError("advancement requires complete original payload")
    def instant(value):
        value = validate_canonical_utc_timestamp(value)
        return value if "." in value else value[:-1] + ".000000Z"
    return replace(event, occurred_at=instant(event.occurred_at)), replace(action, created_at=instant(action.created_at))


@dataclass(frozen=True, repr=False)
class _PreparedAdvancementReceipt:
    stores: object
    guard: object
    workspace: object
    request: object
    run: object
    plan: object
    current_projection: object
    desired_projection: object
    event: object = None
    action: object = None
    slots: tuple = ()
    evidence_read: object = None
    proof_footprint: object = None
    read_bounds: object = None
    receiver_truth: object = None
    transfers: tuple = ()

    def with_records(self, event, action):
        return self.stores.configuration_acceptance._bind_records(self, event, action)

    def _validate_records(self, event, action):
        from control_plane_kit_operations.revision_history import historical_advancement
        historical_event, historical_action = _history_records(event, action)
        expected = historical_advancement(workspace_id=self.workspace.workspace_id,
            session_id=self.request.identity.session_id, plan_id=self.plan.plan_id,
            plan={name: getattr(self.plan, name) for name in (
                "base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision")},
            request_id=self.request.identity.request_id, run_id=self.run.run_id,
            projection_digest=self.desired_projection.projection_digest, events=(historical_event,), actions=(historical_action,))
        if expected["state"] != "accepted":
            raise OperationsRecordError("advancement requires exact original receipt")

    def require(self, connection, workspace_id, *, after_cas=False):
        self.stores.configuration_acceptance._require_issued(self)
        if (self.stores.connection is not connection or self.workspace.workspace_id != workspace_id
                or self.event is None or self.action is None):
            raise OperationsRecordError("advancement requires prepared original owner")
        self.stores.graphs._require_receiver_lifecycle(self.guard, workspace_id)
        expected = self.workspace
        if after_cas:
            expected = replace(expected, current_graph_id=self.plan.desired_graph_id,
                current_realized_projection_id=self.desired_projection.projection_id)
        if (self.stores.workspaces.get(workspace_id) != expected
                or self.stores.execution.get_request(self.request.identity.request_id) != self.request
                or self.stores.execution.get_run(self.run.run_id) != self.run):
            raise OperationsRecordError("advancement owner truth changed")
        self.stores.configuration_acceptance._require_current(self)
        self._validate_records(self.event, self.action)


def _require_prepared_advancement(prepared, connection, workspace_id, *, after_cas=False):
    if type(prepared) is not _PreparedAdvancementReceipt:
        raise OperationsRecordError("advancement requires prepared original owner")
    prepared.require(connection, workspace_id, after_cas=after_cas)
