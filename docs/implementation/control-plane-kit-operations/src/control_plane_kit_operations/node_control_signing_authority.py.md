Source: [control-plane-kit-operations/src/control_plane_kit_operations/node_control_signing_authority.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_signing_authority.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner reloads the complete transit/workload signing-authority pair for one
retained node-control attempt. It validates current local lineage, active keys,
retained secret-use authorization chains and unsigned grant claims, then returns
public keys plus reference-only secret-resolution grants. It does not resolve
private keys, produce signed tokens, send the command or record an execution
outcome. The durable attempt and original authorization were created elsewhere;
reload is not a new approval or effect-attempt workflow.

ReloadNodeControlSigningAuthority contains only a repr-hidden attempt_id, with
exact-str bounded identifier validation. execute requires the exact command type;
its constructor accepts only a UoW factory and keyword-only epoch clock. It takes
no caller scopes or credentials, so outer composition must authenticate and
authorize access to retained attempts. The service checks retained authority,
not the identity of an arbitrary caller who possesses an attempt ID.

DeferredNodeControlSigningRequest is a frozen, slotted pair of exact transit and
workload request types plus attempt/actor, current graph/projection, correlation
and public-key fingerprint anchors. Its identifiers are exact str values using
the local 1..200-character grammar; actor has a lowercase-leading 1..128-character
grammar and fingerprints are lowercase SHA256 text. Both grants must be exact
family types and agree on target, variable, operation, codec, request/idempotency
IDs, digest and issued/not-before/expiry times. Transit attempt/workspace/graph
coordinates must agree, including current_graph_id. The realized-projection ID
has no matching field in those grants; construction validates its syntax, while
reload compares it with durable workspace lineage.

The family authority values wrap a public key and SecretResolutionGrant, with
their fields hidden from repr. Their own checks use isinstance; the containing
NodeControlSigningAuthorityPair requires exact family/deferred types and exact
public-key/resolution-grant types. Each key must be Ed25519 with the grant's key
ID and the deferred fingerprint. Each resolution must match authorization ID,
workspace, family intent, actor, correlation and attempt operation_id, with no
session/run/activity/effect/probe provenance. This prevents selected substitutions,
but a directly constructed pair does not look up active providers/references or
prove that its public key corresponds to resolved private bytes.

Reload enters one UoW and reads the
[intended attempt](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_attempts.py).
Missing/corrupt/wrong-type attempts become a fixed unavailable error. The actual
attempt store's get is a normal retained-record read; it is not a worker claim or
an execution lease. The attempt value binds canonical command and unsigned
transit/workload request coordinates and bounded serialized material. Reload does
not issue a new request, replace its grants or establish that it has not already
been dispatched by another service.

Next it locks the workspace and requires current authored/realized lineage to
match the attempt. It does not decode the graph, inspect desired lineage, reload
the gateway runtime/address or choose a fresh transit gateway. The later transit
verifier receives expected_gateway_node_id from the retained transit grant itself;
this is not an independent live topology lookup. Retained intent construction and
other delivery boundaries remain responsible for their own graph/target laws.

For each family the actual
[key store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
takes a shared workspace-purpose advisory transaction lock and selects at most
two active rows across issuers, requiring exactly one. Transit is selected before
workload. Normal lifecycle writes take the corresponding exclusive purpose lock;
this coordination is stronger than an unlocked active-key read but is not a proof
about arbitrary SQL writers. Reload requires exact registered-key type, expected
workspace/purpose, retained registration ID, a recomputed registration identity,
and issuer/key ID matching the unsigned grant. Registration derivation binds
public material/reference identity without resolving private bytes.

The private
[signing-authority store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_signing_authority_store.py)
is supplied by the Postgres store bundle, not imported by this public service.
Its selected get_for_share query joins both retained authorizations to their
active reference/provider records with workspace/reference/provider linkage,
then FOR SHARE locks all six aliases in one query. Missing or malformed selected
truth becomes a private store error that reload translates. These SQL details
were inspected as dependencies; the separate store and database-test companions
are not part of this authored/reviewed batch.

For each chain, the service reruns
[reference admission](secret_providers.py.md): matching provider/workspace,
reference prefix and allowed intents. It constructs a resolution grant from the
retained authorization and current provider, then checks authorization/reference/
provider IDs and reference equality with the selected key's private reference.
Intent, actor, correlation and operation must match the attempt, and all other
optional provenance must be absent. Active statuses are provided by the actual
joined selector; the public helper does not replace that store contract with a
separate universal status validator. No new secret-use authorization is written.

After those locks/checks, the epoch clock must yield an exact integer in
0..2**53-1. Actual Core
[transit verification](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py)
and [workload verification](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
compare unsigned claims with the retained request, issuer/key/attempt or workload
audience, request digest and command coordinates. They accept not_before <= now
< expires_at. The workload key ID is additionally compared by this service.
These helpers perform no cryptographic signature verification or network IO;
they do not mint a fresh validity window or extend expired grants.

Only after both families pass does the service request commit. Actual UoW exit
commits/closes and releases locks before _pair constructs the returned shared
request and authorities. A failed reconstruction produces no partial authority
pair, but can occur after the read transaction has committed. There are no durable
writes or rollback compensation owned by this service. Current local truth or
grant time can change after return; the pair is a checked snapshot, not a lock
held through future resolution/signing/delivery. Downstream effect boundaries
must retain their own authority/validity rules.

The shared deferred shape checks agreement of supplied unsigned grants, not their
cryptographic origin or full correspondence to a separately supplied command.
The reload path gains stronger guarantees from the typed stored attempt and Core
verification. Similarly pair construction does not independently compare every
provider/endpoint/credential/reference field to persisted truth; the locked chain
and _resolution checks supply that relationship. Core SecretResolutionGrant is
reference-only data and carries endpoint/credential/reference IDs, not the bytes
they designate. Its presence alone is not successful provider resolution.

Expected lookup/shape/policy errors are translated to fixed unavailable messages,
typically raised after the relevant handler to avoid retaining cause/context.
Public aggregate constructors similarly bound selected attribute/type/value
failures. Unexpected adapter/database/clock exceptions outside those catch sets
can propagate; this is not universal log scrubbing. Hidden repr fields suppress
keys/resolution details and actor/correlation/fingerprint anchors, but the visible
deferred grants still contain unsigned operational claims. Field access or generic
serialization does not inherit repr redaction.

The [assigned tests](../../tests/test_node_control_signing_authority.py.md)
exercise public shapes, selected pair/deferred substitutions, error/repr bounds
and literal source/import rules using synthetic values. They never execute reload,
use fake/real stores or call a provider. A checkout search found direct service
references in this owner, root exports and tests; no external server/interpreter
composition is proved here. The separately located PostgreSQL suite is not
credited as reviewed or passing evidence for this batch.

Read depth: full 622-line owner and 793-line assigned test. Selected actual attempt/
deferred contracts, Core unsigned verifiers/resolution grant, provider admission,
key selector/locking and identity derivation, private joined selector and store
bundle wiring were inspected with retained key/provider/UoW context. No executable
validation, source/pin change, credential/key, database/provider/runtime action
or publication occurred for these notes. Documentation adds no security surface.
