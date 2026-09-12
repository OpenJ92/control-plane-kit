Source: [control-plane-kit-operations/src/control_plane_kit_operations/workflows.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 512-line module owns transport-neutral grouped operator intent: start a
session, record a manual action, close a session or cancel it. Its command service
interprets those values into durable session/action records through an injected
unit of work. It does not own workspace creation, topology edits, plan generation,
approval, execution-run transitions or runtime effects. Recording an action whose
kind is SET_DESIRED_GRAPH or REQUEST_ACTIVITY_PLAN records history; it does not
dispatch that other service. Cancelling a session does not cancel a deployed
resource or an execution run.

The [Operations root](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py)
imports the command classes, key, service, result and workflow errors. OperationCommand
is the local union of StartOperationSession, CloseOperationSession,
CancelOperationSession and RecordOperationAction. execute uses isinstance dispatch,
so this is a typed command family, not exact-class enforcement against subclasses.
Unsupported inputs raise InvalidOperationCommand. This module declares no __all__.

IdempotencyKey wraps a string that must contain some non-whitespace text and be at
most 200 characters. It does not strip or normalize the supplied value. The same
nonblank-text helper checks command workspace/session/actor/title fields; those
command checks do not impose the later record layer's 512-character/control-character
rules. Constructors require an IdempotencyKey instance rather than a raw string.
Close and cancel carry session, actor and key; start additionally carries workspace,
title and string-valued metadata; manual record carries a typed action kind and
mapping payload.

RecordOperationAction accepts OperatorCommandKind, not arbitrary strings or the
broader record layer's LifecycleOperationKind. It reserves START_OPERATION_SESSION,
CLOSE_OPERATION_SESSION, CANCEL_OPERATION_SESSION and RECORD_OPERATION_ACTION for
their owning paths. Other accepted kinds still mean recorded intent here. Payload
top-level keys must be strings; start metadata has string keys and string values.
The frozen dataclasses do not deep-freeze or copy their mapping fields. Validation
at construction therefore is not an immutable snapshot guarantee for later use.

The secret-key guard recursively visits mappings and lists/tuples, rejecting keys
containing secret, token, password, private_key, credential or api_key unless the
normalized key ends in _ref. It checks key spelling, not whether a value contains
a secret; it does not validate reference existence or custody. Diagnostic paths
contain supplied key names. There is no recursive depth/size budget or universal
secret-value detector here. Accepted mappings are retained for fingerprinting and
persistence; they are not replaced with redacted values in the records.

Command descriptor methods emit the command identity, coordinates and key. Start
metadata and manual payload values become <redacted> under sorted top-level keys;
close/cancel use the shared transition descriptor. These methods retain key names,
actor/title/identities as applicable. They are an explicit display representation,
not permission to expose raw command/result objects. OperationCommandResult holds
the session, action and replayed flag without its own post-init validation; its
descriptor selects session/status/action/kind/ordinal/replayed rather than raw
metadata or payload. It is not a provider observation or an authorization receipt.

The service takes a unit-of-work factory, clock and ID factory. It does not create
connections or runtime clients itself. The injected objects supply infrastructure;
the workflow body decides the transaction's grouped intent. _start enters a unit
of work, calls start_in_unit_of_work, requests commit and returns. The public
composition helper neither opens nor commits the caller's transaction.

Start computes an intent fingerprint within that transaction, reads workspace
truth and translates a missing workspace KeyError to OperationWorkspaceNotFound.
It then takes a workspace/key session-idempotency lock before checking whether the
session exists. This serializes concurrent starts before a session row is available.
An existing session must have the same fingerprint; its initial action must exist
under the session/key lookup with the same fingerprint. Otherwise the path raises
OperationIdempotencyConflict. Replay returns that existing session and action with
replayed=True, without sampling the clock or allocating new IDs.

A fresh start allocates a session ID, samples the clock, builds an OPEN session,
then allocates the initial action ID. The START_OPERATION_SESSION action has ordinal
one, payload containing workspace_id, the same timestamp and the same key/fingerprint.
Both rows are inserted through activity_history. The result returns these constructed
records; it does not reread their stored form. If the action insert fails after the
session insert, the caller transaction must roll back both.

The [saved-preparation caller](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/saved_deployment_preparation.py)
illustrates why the composition helper exists: its selected start path combines
saved-revision admission/session creation with source-record association in one
unit of work, validates the returned evidence and requests commit itself. On replay
it also checks saved source evidence. Those admission/lineage checks belong to that
caller, not to generic session start. No complete saved-preparation review is
implied by this selected call-site inspection.

Manual and terminal commands fingerprint before entering their unit of work. Both
take the session/key action-idempotency lock and read existing action evidence first.
If an action exists, its fingerprint must match. The service requests commit, reads
the current session and returns it with the existing action and replayed=True.
It does not require OPEN on this replay branch, allocate an ordinal or append an
action. Thus a replay result can contain a later session state alongside the original
action; it is not a historical session snapshot. The helper verifies fingerprints
and lookup presence rather than independently reconstructing every stored action
field or revalidating a full durable evidence chain.

For a fresh manual action, _get_session_for_update locks and reads the session,
translating a missing row to OperationSessionNotFound. Status must be OPEN. The
service allocates an action ID, obtains next_action_ordinal, samples the clock and
constructs a record containing the requested kind and payload. It inserts the action,
requests commit and returns the locked session value plus the new action. There is
no workspace pointer update or interpretation of graph_id inside that payload.

For a fresh close/cancel, the same lock/replay/OPEN sequence precedes ID, ordinal
and clock allocation. The action records the selected lifecycle kind and previous
status. transition_open_session conditionally changes the session to CLOSED or
CANCELLED with closed_at equal to the action timestamp; no matching OPEN row raises
OperationSessionStateConflict. The action insert and status update share the same
transaction. Terminal state is write-once through this path; there is no reopen or
automatic compensation branch. No comparison with created_at or other timestamps
enforces clock monotonicity here.

The actual [history adapter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
owns SQL access on a supplied connection. Session-start idempotency uses a
transaction-scoped advisory lock derived from operation-session:workspace:key;
action idempotency uses operation-action:session:key. Fresh action/terminal paths
then acquire the authoritative session row with SELECT FOR UPDATE. Ordinal selection
locks that row again and calculates MAX(ordinal)+1. Holding the same transaction
through insertion matters; the ordinal helper is not an independently durable
reservation. Terminal SQL updates only status=open and returns the updated record.

The [schema](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
backs these paths with session/action primary keys, unique session/ordinal, partial
unique indexes for non-null workspace/start-key and session/action-key pairs, and
session/workspace foreign keys. It restricts session status/closed_at consistency
and action kind vocabulary. Advisory locks coordinate the service before inserts;
constraints remain authoritative when a write violates durable truth. Raw store
methods do not replace workflow OPEN/replay/intent policy, and this service does
not hide additional commit ownership in the adapter.

The actual [PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
binds stores to one connection. commit() marks a request; connection.commit() runs
only on successful context exit. A body exception or missing commit request causes
rollback, a commit exception attempts rollback, and exit closes the connection.
Consequently the replay branch's session read after commit() still belongs to the
same transaction. The service does not translate an ambiguous external commit into
success or prescribe blind retry; SQL/connection failures can escape to the caller.

_fingerprint hashes UTF-8 compact sorted JSON with SHA-256. Start intent contains
the start tag, workspace, actor, title and raw metadata. Close/cancel use distinct
tags, session and actor. Manual intent contains the record tag, session, actor,
action kind and raw payload. The lookup key is deliberately separate from this
digest; generated IDs and clocks are absent. JSON TypeError/ValueError becomes
InvalidOperationCommand with the original cause. This is the Python JSON encoding
used by this service, not a separate canonical JSON standard or finite-number/
bounded-payload validation layer. JSON-equivalent inputs need not preserve every
Python container distinction in the fingerprint.

The [records](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
add durable-shape checks: bounded nonempty text without characters below ASCII 32,
typed session status, OPEN with no closed_at or terminal with one, positive exact-int
action ordinals, and a closed command-or-lifecycle action kind. Metadata/payload
remain Mapping values. Session/action constructors check timestamp text shape as
ordinary record text; the history adapter's
[temporal codec](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py)
requires canonical UTC text when writing PostgreSQL timestamps and decodes aware
datetimes on reads. These layers should not be described as identical validation.

OperationCommandError is the shared RuntimeError base. Its variants distinguish
invalid command input, idempotency conflict, missing session, invalid session state
and missing workspace. Lookup translation uses explicit exception causes; it does
not erase the original KeyError or guarantee bounded exception chains. Record,
JSON adapter, database, ID-factory and clock failures are not all translated into
this family. _get_session is also defined as a translating read helper, but current
execution paths use _get_session_for_update for fresh actions and direct store reads
on replay; do not infer that every missing replay session uses that helper.

The governing [workflow tests](../../tests/test_workflows.py.md) contain fifteen
tests under a PostgreSQL fixture. They check start storage/replay/conflict, missing
workspace, manual ordinals and intent replay, terminal rejection/cancel replay,
concurrent starts, both manual/close orderings, eight close/cancel races, progress
in an independent session, one reserved-kind constructor case, late action-ID
collision rollback and selected query/record behavior. The test companion records
each assertion's limit. A short future timeout is not proof of a particular SQL
wait; the timestamp/ID query fixture does not distinguish timestamp sorting from
ID-only sorting; the collision test checks new-session absence, not every table.

Those tests do not exhaust command validation, deep mutation of mappings, replay
after every terminal state, stored-evidence tampering, every reserved kind, clock
failure, commit ambiguity or secret-redaction behavior. No fresh executable test
result is claimed here. Operational history is the session/action pair with its
key, fingerprint, ordinal and timestamps. This owner does not emit runtime events,
advance graphs, retain provider logs or supply history retention/cleanup policy.

Security boundary: actor IDs are recorded data; this module takes no principal,
scope set or authentication token and performs no access-control decision. Callers
must establish authorization before commands reach it. Descriptor redaction and
secret-shaped-key checks have the limits above; returned records can contain raw
allowed values. No network exposure, credential custody or provider mutation is
introduced by this companion. Future transports must preserve grouped transaction
ownership and avoid interpreting replayed history as permission for new effects.

Read depth: complete 512-line owner, every helper and command path; complete 504-line
workflow suite/all helpers; complete PostgresUnitOfWork and temporal codec. The
890-line history owner was read in full for its companion, with relevant session/
action SQL and decoders rechecked for this pair of notes. Selected actual root
exports, record validators/status, Core command vocabulary, store binding, schema
constraints/indexes and saved-preparation composition were inspected. No full
records, Core commands, schema SQL or saved-preparation review is claimed here.
Static documentation checks cover links, whitespace and frozen source/tests only.
No executable imports/tests, database/provider calls, credential access, source/
inventory edits, publication, merge or live work were performed.
