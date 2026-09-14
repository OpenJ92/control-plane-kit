Source: [control-plane-kit-core/src/control_plane_kit_core/node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py).
Maintain this document alongside its source file. Recheck nominal references,
state/operation/result compatibility, canonical wire identity, grant comparison
and actual authorization/workload consumers when changing this language.

This 2030-line module defines the pure workload variable language: typed public
state, read/apply requests, unsigned workload grants, bounded results and static
variable/surface declarations. It does not authenticate a caller, sign or verify
a signature, retain replay state, acquire a workload lock, evaluate the current
version, mutate routing or persist a command. Its direct package dependencies
are the shared public-wire helper, CapabilityName and ControlRouteSetName;
hashing, JSON parsing and numeric checks use standard library facilities.

## Objects and transformations

The design has three state forms, two operations and four result variants.
Commands carry a complete replacement state and, for mutation, an explicit
expected-version precondition. There is no arbitrary HTTP method/body, dynamic
method invocation or provider operation embedded in this syntax.

| Variable kind | State codec | Apply command codec |
| --- | --- | --- |
| SCALAR | control.scalar.v1 | control.replace-scalar.v1 |
| MAP | control.map.v1 | control.replace-map.v1 |
| WEIGHTED_ROUTING | control.weighted-routing.v1 | control.replace-weighted-routing.v1 |

READ_STATE uses no command codec and returns control.state.v1. APPLY_COMMAND
requires the kind's replacement codec and returns control.transition.v1. These
tables define valid compositions, while actual read/transition interpreters own
state access and effects. Canonical request bytes become a request digest; a
grant binds that digest to target, operation, issuer/audience and time claims.

## Public identities and graph references

NodeControlGraphReference pairs one of six roles (workspace, graph revision,
node, provider socket, variable, target) with a repr-hidden identifier. The
constructor validates role and text. NodeControlTarget requires the corresponding
four nominal references for workspace/revision/node/socket; variable fields and
weighted targets require their specific roles. A descriptor flattens references
to text, and decoding restores roles from each field's semantic position. Neither
construction nor decode proves that the referenced entity exists in a graph.

The imported [public-wire owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py)
admits identifiers of 1..128 ASCII characters, beginning with a letter/digit and
continuing with letter/digit/dot/underscore/hyphen. Its reference form adds colon
and slash and permits up to 256 characters. Both apply the shared public-material
filter. Digest values require 64 lowercase hex characters. Epochs require exact
nonnegative ints within 2**53-1. This module's versions use the same numeric range.

The public-material filter checks literal text and one ASCII percent-decoded
projection for selected authorization/credential assignments, private-key armor,
compact-token prefixes and endpoint-shaped content. Endpoint checks include
schemes, protocol-relative addresses, valid-range host:port forms, IP literals
and localhost forms. This is a defined finite recognition policy, not proof that
every possible secret or internal name is absent. Shape admission may reject a
value before its material category is considered. Descriptions allow nonblank
public text up to 512 characters with no NUL; scalar strings use the narrower
identifier grammar, not the description grammar.

workload_node_control_audience requires NodeControlTarget and constructs
workload:{node}:{socket}, then validates it as a reference. Workspace and revision
are not part of that audience string; the grant comparator checks them separately.
The combined string must fit 256 characters even when each target component
separately fits its own bound. Audience derivation is not endpoint resolution or
proof of a trusted verifier.

## State snapshots and payloads

ScalarControlState permits None, exact bool, safe-range exact int, finite exact
float other than negative zero, or an admitted identifier string. Float magnitude
is not restricted to the safe-integer range. MapControlState requires a tuple of
at most 128 key/value tuples, validates identifier keys and scalar values, rejects
duplicate keys and sorts entries. Empty maps are permitted. Their frozen value
representations carry scalars/tuples; descriptors expose a new dictionary form.

WeightedRoutingControlState requires a nonempty tuple of at most 128 unique
TARGET references and a tuple of target/weight pairs. Weights must cover exactly
the target set with no duplicate weight targets. Exact ints must be nonnegative
and safe-range; exact floats must be finite, nonnegative and not negative zero.
Booleans and other types are rejected. At least one weight must be positive.
Accepted weights normalize to floats; targets and pairs are sorted. This does
not normalize the sum to one, choose a target, resolve graph membership, lock a
balancer or guarantee an atomic runtime update. The snapshot is atomic as a
represented value; the interpreter must make its application atomic.

NodeControlPayload requires a closed replacement command codec and its matching
state class. Its complete canonical descriptor must fit 16,384 bytes. Standalone
state constructors impose their own item/value rules, rather than independently
enforcing that aggregate payload limit at every state boundary. A precondition is
one bounded nonnegative expected_version; construction does not compare it with
workload state or reserve a version.

## Requests and canonical wire identity

NodeControlCommandRequest requires typed target and VARIABLE reference, a closed
operation, bounded request/idempotency identifiers and the sole canonicalization
identity jcs-rfc8785.v1. READ_STATE forbids command codec, precondition and payload.
APPLY_COMMAND requires all three and requires payload.codec to be the same command
codec. The complete request descriptor is bounded to 16,384 canonical bytes.
These local checks do not match a deployed variable declaration or grant caller
permission. An idempotency key is data until another owner records/enforces it.

The descriptor includes all nine fields, including explicit null command fields
on reads. canonical_bytes uses the shared RFC8785 encoder; canonical_digest is
SHA-256 of those bytes, returned as NodeControlRequestDigest. It has no additional
domain prefix in this method. A well-formed digest wrapper alone does not prove
that anyone computed it from the intended request.

Mapping decode requires exact keys, typed nested target/precondition/payload
shapes and known enum strings, then invokes normal constructors. It may normalize
map/weighted ordering. Raw-byte decode is stricter: exact bytes type, input-size
cap, UTF-8 JSON object parsing, duplicate-key rejection at every object, rejection
of JSON constants, constructor validation and byte-for-byte equality with the
canonical re-encoding. Alternate whitespace, key order or numeric spelling does
not gain acceptance merely because it parses to an equivalent value.

The shared parser recursively observes containers so recursion failures are
normalized. Safe integer tokens stay integers; larger integer-looking tokens
become floats so a canonical binary64 spelling can be represented. Version/epoch
fields still require exact ints. Scalar/weight validation rejects nonfinite values
and negative-zero float values, and exact re-encoding handles noncanonical token
spellings. Mapping APIs and raw-byte APIs have different admission contexts; do
not credit the raw input cap or duplicate-key detection to arbitrary mappings.

## Unsigned workload grants and comparison

DelegatedWorkloadNodeControlGrant carries issuer, key ID, audience, target,
variable, operation/optional command codec, request/idempotency IDs, typed request
digest, issued_at/not_before/expires_at and JTI. Read/apply codec rules match the
request operation distinction. Epochs are safe-range exact ints; not_before must
not precede issued_at, expires_at must follow not_before, and expires_at-issued_at
must be at most 300 seconds. Construction and canonical_bytes enforce a separate
2,111-byte grant cap. The grant contains no signature or resolved secret value.

Its mapping codec requires all fourteen exact fields; raw-byte decode uses the
same strict parsing/re-encoding discipline at the smaller size cap. The grant
digest is SHA-256 of the complete canonical grant without an additional domain
prefix here, wrapped as WorkloadNodeControlGrantDigest. Request/grant digest
classes are distinct even though both store lowercase hex text.

verify_workload_node_control_grant validates the request and expected issuer,
audience and clock arguments, then returns the first applicable rejection in
this order: grant type, issuer, audience, half-open time interval
not_before <= now < expires_at, workspace, revision, node, socket, variable,
operation/command codec, then request ID/idempotency key/current request digest.
The result is an exact bool plus no code on acceptance or one of eleven closed
rejection codes on failure. Bad verifier inputs can raise a contract error rather
than returning a grant rejection.

The comparator does not verify key ID against a trusted key, JTI replay, signature,
key revocation, graph membership or actual workload version. It relies on normal
grant construction for epoch ordering/lifetime; acceptance is unsigned claim
agreement with caller-supplied expectations. It also does not derive the expected
audience itself. Trusted caller composition and the cryptographic/replay boundary
must supply the missing authority checks.

## Result algebra and variable-aware decoding

NodeControlEvidence carries exactly one closed code, with no free-form message,
provider traceback, state or credential field. Results admit these combinations:

| Result form | Operation/status | Data/evidence |
| --- | --- | --- |
| NodeControlReadStateSucceeded | READ_STATE/SUCCEEDED | Matching state codec, bounded version and typed state; no evidence |
| NodeControlTransitionSucceeded | APPLY_COMMAND/SUCCEEDED | Bounded version and APPLIED or NO_CHANGE; no state |
| NodeControlRejected | READ_STATE/REJECTED | NOT_AUTHORIZED |
| NodeControlRejected | APPLY_COMMAND/REJECTED | PRECONDITION_FAILED, INVALID_COMMAND or NOT_AUTHORIZED |
| NodeControlFailed | Either operation/FAILED | Computed INTERNAL_FAILURE evidence; no version/state |

All forms validate request identity and their own compatibility and size rules.
Properties derive fixed status/operation/result-codec choices where applicable;
callers cannot supply contradictory fields through those constructors. Failure
does not store an arbitrary underlying exception. Returned versions are bounded
values, not proof of a version increment or precondition success.

NodeControlResultCodec is constructed with a ControlPlaneVariableDescriptor.
Encode/decode checks the variable's per-operation result codec; successful reads
also require its state codec. Decode selects exact field sets for the result
variant and invokes its constructor. The decoder does not bind request_id to a
specific request, compare a returned version with a precondition, verify a signed
response or attest that a mutation happened. There is no raw-byte result codec
in this module analogous to the request/grant raw-byte APIs.

## Variable and surface declarations

ControlPlaneVariableOperationContract binds READ_STATE to no command codec and
STATE_V1, and APPLY_COMMAND to a command codec and TRANSITION_V1. A variable must
contain exactly the ordered pair READ_STATE then APPLY_COMMAND, not an arbitrary
subset or permutation. Its kind fixes the state/apply codec pair from the table.
route_set and capability are derived as NODE_CONTROL and NODE_CONTROLLABLE;
decoding rejects other literal labels. The optional description is repr-hidden
but retained in the public descriptor. A standalone variable descriptor does
not perform the aggregate surface byte check or register handlers.

WorkloadNodeControlSurfaceDescriptor names one PROVIDER_SOCKET reference and a
nonempty tuple of at most 128 typed variables, sorts by variable name, rejects
duplicate names and caps its complete canonical descriptor at 16,384 bytes.
Its mapping codec checks exact keys/list shape and constructor constraints;
encode also repeats the size check. The exported sixteen-surface ceiling is a
composition obligation enforced by consuming block/product contracts, not a
limit on the number of independently constructed surface objects here.

The root facade exposes the public language. The adjacent surface-read language
wraps a static surface with its own profile and identity; gateway transit uses
related target/request values with distinct authority meaning. Neither should
be substituted for this workload end-to-end grant. This module does not define
the SDK's stateful variable registry, HTTP handlers or relay implementation.

## Actual consumer boundaries

[BlockSpec](../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py)
checks typed, unique, sorted surface declarations and agreement with the
node-controllable capability, including the sixteen-surface cap. ProductRuntimeContract
also checks that declared control sockets exist and use HTTP. The graph validator
reports missing or non-HTTP provider sockets for block surfaces. These are graph
composition checks outside a standalone NodeControlTarget/Surface constructor.
The [route owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py)
declares GET capabilities/status/variable and POST variable commands under
/__control, with distinct surface-read/read/apply scope labels. They are protocol
declarations, not evidence of an installed route or enforced authentication.

Selected [Operations intent authorization](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py)
locks request identity, compares existing intent fingerprints or prepares new
intent in a unit of work, and commits before returning preparation. New intent
checks workspace/current lineage and realized projection, gateway/target HTTP
sockets in one runtime, a declared target edge, surface variable and operation
codec. It selects distinct gateway-transit/workload signing authorities, constructs
both grants and records key-use authorization references in the intended attempt.
Those are selected actual service-body checks, not duties performed by Core.

The [intended-attempt record](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_attempts.py)
checks request/grant identity agreement, current graph correlation and byte bounds.
The selected [signing-authority reload service](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_signing_authority.py)
reloads current facts in a transaction and calls this unsigned comparator; it
separately checks the workload grant key ID against the selected key. Returning
reference-only signing material is not a signature or a workload mutation.
The [SDK boundary ADR](../../../../adr/0010-node-control-route-prefix-and-server-sdk.md)
assigns variable implementations and workload verification/dispatch outside Core.
That is an architectural assignment, not a claim that an SDK was executed or
that a particular installed server implements it.

## Tests, diagnostics and review limits

The full [592-line primary suite](../../../../../control-plane-kit-core/tests/test_node_control.py)
contains fourteen tests and seven construction/lookup helpers. Its
[companion](../../tests/test_node_control.py.md) distinguishes computed mapping
round trips, a digest-length check, selected state/reference rejection examples,
exact audience ceiling/disclosure cases, unsigned claim mismatch examples,
selected temporal values and route/export declarations from execution evidence.
Expected version 4 and a constructed success version 5 do not execute a transition.

Previously reviewed companion navigation separately covers canonical-wire,
workload-byte, public-material, nominal-reference, operation-contract, result-variant
and surface suites. Their fixed fixtures/negative cases are not all assertions of
the primary suite and were not all reread in this owner pass. Shared-wire and
canonical-helper source checks do not substitute for executing another language's
SDK or establishing installed dependency versions.

The mapping helpers reject unknown/missing fields without echoing field names.
Known enum, canonical-domain and raw JSON parse failures are normalized after
their exception handlers, so those paths do not retain the caught exception as
cause/context. They do not catch every unexpected collaborator error. Many
constructors use isinstance rather than exact nominal type checks, and size
validation can invoke a nested value's descriptor; this is not a sandbox for
hostile Python subclasses/custom mappings. Public-material rejection and selected
repr-hidden fields also do not make all descriptors confidential: admitted state,
references, request IDs and unsigned grant claims are represented intentionally.

Authoring read all 2030 owner lines, the full 163-line shared-wire helper, full
primary suite/helpers, actual route/capability/facade declarations and selected
block/product/graph, adjacent surface and Operations consumer bodies. Only this
owner gains coverage. Security and operational history: no source, runtime,
network, auth, secret or mutation behavior changed; no locks, replay records,
signatures or provider/workload observations were produced. Links, whitespace
and frozen source/test consistency were checked. No application imports,
executable tests, database/provider calls, source changes or merge occurred.
