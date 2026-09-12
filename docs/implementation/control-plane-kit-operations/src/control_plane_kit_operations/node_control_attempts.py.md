Source: [control-plane-kit-operations/src/control_plane_kit_operations/node_control_attempts.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_attempts.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner describes retained node-control intent: one command, its unsigned
transit/workload grants and the durable identities used to prepare them. It owns
NodeControlIntendedAttempt, the semantic fingerprint function, bounded selector
validation and the error family shared with its
[PostgreSQL store](postgres/node_control_attempt_store.py.md). It does not execute
the command, resolve or sign with a private key, or record a delivery result.

NodeControlIntendedAttempt is a frozen, slotted dataclass with 15 constructor
fields: attempt/actor, authored/realized current lineage, gateway runtime, two key
registration IDs, two secret-use authorization IDs, two correlation IDs, intended
timestamp, request and two grants. Workspace and request IDs derive from the
request. Request/grant bytes and intent fingerprint are properties computed from
the supplied values, not separately caller-settable constructor fields or cached
wire snapshots. Dataclass equality includes all retained fields; equality of the
smaller semantic fingerprint does not imply equality of the entire attempt.

Construction requires canonical UTC intended_at through the actual
[timestamp validator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_temporal.py).
It accepts isinstance-compatible request/grant objects, rather than requiring
their exact classes. Exact-str identifier checks cover attempt, graph/projection,
runtime and correlation anchors, request workspace/request ID and both grants'
key IDs/JTIs. These use the 1..200-character ASCII identifier grammar. Actor and
issuer use a lowercase-leading 1..128-character grammar; key registration and
authorization IDs require dkey_/suse_ plus 64 lowercase hex characters.

The aggregate requires 1..16384 canonical request bytes, 1..2834 transit grant
bytes and 1..2111 workload grant bytes. Both grants must match the request's target,
variable, operation, command codec, request ID, idempotency key and canonical
digest. Transit additionally matches attempt ID, workspace and graph revision;
current_graph_id must equal the request graph revision. Core constructors own
the internal request/grant laws. The aggregate's additional cross-value comparisons
do not require equal issue/not-before/expiry times between families, look up the
projection/runtime, verify audience against live topology, check grant expiry
against a clock or prove cryptographic origin. The selected test explicitly changes
only transit expiry while preserving construction and the fingerprint.

All ordinary Exception failures inside __post_init__ become the fixed incoherent
attempt error, raised after the handler so rejected material is not retained as
cause/context. This covers constructor validation, not every later property call
or external adapter exception. The frozen wrapper is a value contract; it is not
a database immutability mechanism or an independent guarantee about arbitrary
subclass implementations of nested methods.

node_control_intent_fingerprint validates actor, a node-role gateway reference
and request type, then SHA256-hashes RFC8785 bytes containing exactly profile
node-control-intent.v1, actor_subject, gateway_node_id and request_digest. Thus the
command's canonical digest binds its own command coordinates, while attempt ID,
runtime/projection ID, authorization/key IDs, grant issuers/key IDs/JTIs/times and
intended_at are outside this fingerprint. Changing actor, gateway node or command
changes the hashed input. It is a reproducible replay comparison, not a signature,
an authorization decision or a digest of all stored authority evidence. Errors
from the initial shape check are fixed; downstream encoding/digest calls are not
wrapped in the constructor's broad exception handler.

The selected actual
[intent-service caller](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py)
checks scopes, computes that fingerprint and locks workspace/request identity in
one UoW. If an attempt exists with the same fingerprint, it returns the retained
attempt and marks replay; a different fingerprint conflicts. It does not regenerate
IDs or grants on that branch. The store itself has no return-existing upsert.
Its separate reconstruction checks preserve the complete stored representation
and selected relational witnesses before replay can use the fingerprint. This is
intent preparation replay, not evidence of at-most-once command execution.

Current authority is a later boundary. The
[signing-authority reload service](node_control_signing_authority.py.md) checks
current lineage, active keys and secret chains, retained provenance and grant time.
Reading or replaying an intended attempt alone does not grant those current powers.
The attempt remains historical intent when the workspace advances; its current_*
fields describe the preparation snapshot, not automatically updated live pointers.

Attempt ID, key/authorization IDs and correlations are hidden from dataclass repr.
Actor, lineage/runtime, intended time, request and unsigned grants remain visible;
nested grant representation can expose operational identities despite a hidden
outer field. There are no private-key or signed-token fields, but command payloads
and operational claims are retained data, not a universally scrubbed log surface.
The error hierarchy separates incoherence, durable identity conflict and corrupt
reconstruction. The private identifier helper validates selectors before SQL and
returns the accepted exact string without touching storage.

Selected assertions in
[test_node_control_attempts.py](../../../../../control-plane-kit-operations/tests/test_node_control_attempts.py)
check derived wire/fingerprint values, changed actor/gateway/command semantics,
fingerprint stability across authority/time identities, accepted read/apply
requests and 18 cross-claim mismatch constructions. Eight selected scalar canaries
and five invalid store selectors assert bounded errors; the selector fake raises
if SQL is reached. Selected real database cases cover complete value round trips,
canonical-byte/digest corruption, relational witness drift, rollback and request
locking. The test called restart opens a fresh database connection; it does not
restart a process or deploy a server. Full test-module review belongs to its
separate future companion.

Read depth: full 214-line owner and 204-line store; selected test fixture/public
assertions at 159..754, database fixture/error helper at 814..990, round-trip and
corruption test at 1036..1174, and relational/rollback/lock tests at 1229..1490.
Actual timestamp validator, selected Core canonical decoders, intent-service replay
branch and schema declarations were inspected, with retained UoW and signing-reload
context. No source/pin change, test execution, database setup, credential/private-key
access, provider/runtime action or publication occurred. Documentation adds no new
security surface or permission to execute a retained command.
