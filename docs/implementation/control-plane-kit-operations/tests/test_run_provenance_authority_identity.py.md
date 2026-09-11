Source: [control-plane-kit-operations/tests/test_run_provenance_authority_identity.py](../../../../control-plane-kit-operations/tests/test_run_provenance_authority_identity.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eight tests preserve the canonical run-ID language across secret-use,
ingress and gateway-checkpoint provenance. They also check selected store inputs
before database access. The tests construct pure values and actual store objects
with a deliberately failing connection; they do not persist evidence or authorize
secret access. A valid provenance string identifies a run syntactically, without
proving that the run exists, belongs to a workspace or currently holds authority.

The direct-carrier matrix covers six positions: AuthorizeSecretUse.run_id,
AuthorizedSecretUse.run_id, CloudflareOwnedIngressResource.source_run_id, its
removed_by_run_id, GeneratedIngressSecretReference.source_run_id and
GatewayKeyRotationDeploymentCheckpoint.run_id. Each must preserve valid strings
of length one and 200. Each rejects the same ten candidates: a slash, a str
subclass, an arbitrary object, True, empty text, leading whitespace, leading dot,
embedded space, newline and length 201. Removal provenance is exercised with
REMOVED status and a removal timestamp so unrelated record constraints are met.

The expected errors are exact owner classes: SecretProviderRegistrationError,
IngressAuthorityRegistrationError or GatewayKeyRotationError. The shared helper
captures BaseException and checks exact type, absence of the nonempty candidate's
str in the error's str, and no cause/context. It does not bound message length,
inspect repr or assert exact message text. Those selected candidate checks should
not be described as universal redaction or complete malformed-input coverage.

The actual [RunId](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py)
delegates to the [canonical predicate](../../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py):
exact str type with an ASCII alphanumeric first character, followed by up to 199
ASCII alphanumeric, dot, underscore, colon or hyphen characters. There is no trim
or coercion. The inspected owners validate with this value, then retain the
original admitted string. Their wrappers catch ValueError and raise their own
categorical error after leaving the handler, avoiding a retained exception
context. Other provenance fields continue to use their own validators; this
file does not make every identifier a RunId.

The [secret-use owner](../src/control_plane_kit_operations/secret_providers.py.md)
permits an absent run ID on both the command and evidence record. The absence
test checks both attributes and the evidence descriptor's run_id remains None.
The node-control case constructs a transit-signing-key secret-use command with
operation_id but no run, session, activity, effect or probe identity, then checks
those five defaults remain None. These are constructor laws, not a call to a
secret-use authorization service or evidence that a presented scope grants access.

Both secret-use and custody correlation helpers are called twice with None and
twice with run-a; equal-input results must match. Each rejects run/bad and the
str-subclass input through the owner-error helper. Their actual source validates
the optional run before building semantics and hashing sorted compact JSON with
SHA-256 under distinct secret-use and secret-custody prefixes. The test does not
spy on the hash call, require exact digest/prefix bytes, compare absent versus
present outputs or prove sensitivity to every other correlation coordinate.

_FailOnDatabaseAccess.execute records its query/parameters and immediately raises
_DatabaseTouched. Three actual ingress transition methods receive run/bad:
mark_removing and mark_uncertain via source_run_id, and mark_removed via
removed_by_run_id. Each must raise the exact ingress owner error with an empty
connection call list. The generated-secret get_by_source test establishes the
same boundary for its malformed source run. These store assertions check error
type and zero access; they do not call the shared candidate/chain helper.

The generated-secret record test first constructs valid frozen evidence, then
deliberately bypasses normal freezing with object.__setattr__ to corrupt its
source_run_id. record must still reject before executing SQL. The actual
[store](../src/control_plane_kit_operations/postgres/ingress_authority_store.py.md)
checks the evidence type and timestamp, then delegates the source-key lookup to
_get_by_source, which validates the run before querying. This protects the
idempotency lookup even when constructor validation has been bypassed. It does
not assert reconstruction of every field, deep immutability, replacement policy,
successful insertion or idempotent replay against existing rows.

For valid run-a, the same three ingress transitions and generated-secret lookup
must let _DatabaseTouched escape. This demonstrates that these selected valid
calls reach the connection and do not translate its sentinel into an identity
error. It does not compare exception object identity or query contents/counts,
and the positive matrix does not include generated-secret record. No method gets
past a successful SELECT, so it supplies no executed update or transaction proof.

The actual [ingress values](../src/control_plane_kit_operations/ingress_authorities.py.md)
validate source/removal run IDs and require removal evidence only on REMOVED
records. Their transition store methods validate the new run input before reading
an existing resource, then use dataclasses.replace for the state change and write
the resulting record. The actual
[gateway checkpoint](../src/control_plane_kit_operations/gateway_key_rotations.py.md)
validates its run ID alongside prepared/accepted evidence consistency. These
source contracts explain the fixture shape, but successful transitions, checkpoint
acceptance and reconstructed persisted rows are outside this file's assertions.

Fixture authority/reference IDs, secret:// references, tunnel/DNS IDs, hostname,
timestamps and graph/checkpoint links are synthetic. AuthorizedSecretUse is
constructed directly with an all-a fingerprint; generated-secret evidence is
constructed without a custody receipt or provider read. Their names do not turn
these objects into proof of an actual authorization, resource allocation or
secret retrieval. Security evidence here is early canonical identity admission
and selected candidate-free owner errors; no live secrets, provider effects,
history persistence, restart or cleanup are exercised.

Read depth: all 378 test lines and helpers; actual secret command/evidence and
correlation functions, owner run validators, ingress evidence constructors,
gateway checkpoint, selected ingress/generated-secret store paths, and full core
RunId/predicate. This documentation records source-level evidence only. No tests
or imports were executed, and no database, credentials or provider were accessed.
