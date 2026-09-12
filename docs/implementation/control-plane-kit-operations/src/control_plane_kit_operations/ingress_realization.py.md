Source: [control-plane-kit-operations/src/control_plane_kit_operations/ingress_realization.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/ingress_realization.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 808-line owner translates AllocatePublicIngress and RemovePublicIngress
activities into provider calls and durable ingress/reference records. It consumes
an ActivityRealizationContext and returns an ActivityExecutionOutcome. The adapter
does not itself claim execution, recheck the execution fence or approval, or append
activity journal events. The selected dispatch path in
[coordinator.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
routes these two operation families to the configured ingress adapter. Returning an
outcome is not independent evidence that a coordinator recorded or accepted it.

Three protocols describe provider-facing shapes. IngressAllocationResult supplies
tunnel ID/name, custody receipt, DNS record ID, hostname and endpoint URL.
IngressOwnedResourceCoordinates supplies the four tunnel/DNS/hostname coordinates
needed by teardown. IngressProviderInterpreter exposes create and teardown with an
authority plus secret resolution and custody grants; create also receives the
named ingress, deterministic allocation name and origin URL. These protocols do not
verify provider implementations or remote ownership. The Operations
[root](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py)
reexports IngressAllocationResult, IngressProviderInterpreter and
IngressRealizationAdapter; the owner has no explicit __all__.

IngressRealizationAdapter is a frozen dataclass without slots. Its dependencies are
a unit-of-work factory, a mapping keyed by IngressAuthorityProviderKind, a callable
clock and an optional secret-use authorizer. Construction copies the mapping into
an ordinary mutable dict. Interpreter create/teardown and authorizer
authorize_resolution attributes are checked for presence, not callable signatures;
the factory is not validated here. Freezing the adapter does not freeze dependencies
or the copied dict. Unsupported operation types produce OPERATOR_REVIEW evidence
with code ingress.operation-unsupported, activity ID and operation class name.

Allocation decodes the pinned desired graph with DEFAULT_GRAPH_CODEC, finds the
named ingress, resolves its target provider socket and requires HTTP protocol plus
an http:// URL prefix. This local check does not establish that the URL names a
private host or enforce a network destination policy. The adapter does not make a
separate whole-graph validation call. It obtains the active authority for the
workspace/reference/requested hostname, selects its interpreter and checks for an
existing blocking owned resource in a separate unit of work.

The selected
[ingress store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/ingress_authority_store.py)
requires an active authority whose hostname pattern permits the request.
get_cloudflare selects the newest matching epoch among ALLOCATING, ACTIVE, REMOVING,
UNCERTAIN and ORPHANED records. A REMOVED epoch does not block a later allocation.
An existing blocking record yields ingress.allocate-conflict. This preflight is
not a reservation held across provider I/O. Selected KeyError, ValueError and
InvalidOperationCommand failures become ingress.allocate-unsupported using
str(error); this is not a catch-all exception boundary.

API-token authorization constructs AuthorizeSecretUse with the worker identity and
scopes, request/session/run/activity/intent-event coordinates, the authority's API
reference and CLOUDFLARE_API_TOKEN intent. The authorizer is required at execution.
The returned value must be a SecretResolutionGrant matching workspace, effect ID,
reference and intent. The adapter does not independently compare every returned
actor, correlation, session, run or provider field.

Generated-token custody uses an active registered secret provider, worker scopes,
CLOUDFLARE_TUNNEL_TOKEN intent and a deterministic generated reference. The selected
[secret-provider helpers](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
check the required scope, workspace/provider identity, admitted intents and reference
prefix membership and construct a correlated grant. The selected
[provider store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
rejects missing or inactive registrations. This adapter uses require_active_registration,
not its separate for-update variant. These are reference/grant operations; the
adapter neither resolves API-token bytes nor stores generated plaintext itself.

SecretProviderRegistrationError maps to a fixed secret.use-not-authorized message;
InvalidOperationCommand in allocation authorization maps to a fixed
secret.resolution-authorizer-invalid message. Other exceptions in that stage become
UNCERTAIN with their class name. Before create, the clock must pass
[canonical UTC validation](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_temporal.py).
A ValueError produces an unsupported timestamp result without calling the provider;
other clock exceptions produce uncertainty. The accepted timestamp is captured
before the provider call and reused for allocation records.

The allocation name combines a cpk- prefix, a sanitized/truncated ingress ID and
12 hexadecimal SHA256 characters over pipe-joined workspace, ingress, run,
activity and event IDs, with an overall 128-character limit. Sanitization uses
isalnum, so it is not restricted to ASCII letters/digits. The generated secret
reference appends the token purpose and a full SHA256 digest of pipe-joined
workspace, purpose, run, activity and event IDs to the authority's reference prefix.
That digest does not include ingress ID. Stable names do not establish remote
idempotency, collision freedom or safe concurrent allocation.

create runs outside the adapter's database units of work. Any create exception
returns ingress.allocate-uncertain with its class name and, when admissible, a
structural provider-failure projection. The adapter does not call teardown on this
path. Cleanup internal to a provider implementation is outside this owner's proof.

After create returns, the adapter constructs CloudflareOwnedIngressResource,
validates the custody receipt, constructs a registered-reference candidate and
generated-secret evidence, then writes all three in one unit of work. The selected
[ingress values](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/ingress_authorities.py)
validate resource fields, lifecycle/status, timestamps and source identities.
Neither this adapter nor the selected record_cloudflare implementation compares
returned hostname or tunnel name with the requested hostname/allocation name;
endpoint_url is later placed in success evidence without such a comparison.

The selected
[Core secret contracts](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
make receipt.matches compare custody ID, provider registration ID and reference.
This is not byte-level custody verification and does not compare receipt status or
version against the grant. Receipt construction separately validates its status and
version fields. The reference-candidate helper repeats receipt matching. Before
writing, the adapter rereads the active provider registration and compares its
endpoint and credential references with the grant, then calls secret_references.register,
ingress_resources.record_cloudflare and generated_ingress_secrets.record. The
resource store rejects a different blocking record and assigns the next epoch when
none exists; generated evidence uses its source coordinates to reject replacement
by different evidence.

The [Postgres unit of work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
requests commit via commit() and performs the physical commit on successful context
exit. Any exception in the adapter's post-create construction or recording block,
including that exit, triggers teardown with the raw allocation object and the same
grants. Even an allocation rejected for malformed coordinates is passed through to
this compensation call. Successful teardown returns FAILED/RETRYABLE with
ingress.record-failed-compensated. A teardown exception returns UNCERTAIN with
ingress.compensation-uncertain, both exception class names and the allocation's
four resource coordinates. There is no distinct branch for an ambiguously completed
database commit and no independent remote teardown verification.

Success evidence is constructed after the recording try block. It exposes provider,
ingress/runtime IDs, hostname, endpoint URL, tunnel/DNS coordinates, lifecycle and
connector_material_recorded=True. Failure while constructing this evidence can
escape after durable commit; it does not enter the preceding compensation handler.
The flag reflects this recording path, not an independent secret-provider probe.

Removal decodes the pinned base graph, resolves the active authority and requires
an ACTIVE owned resource. It constructs a teardown plan, selects an interpreter,
authorizes API-token use, loads generated-secret evidence using the resource's
original source run/activity/event coordinates and grants custody for that retained
reference. These steps happen before testing whether the plan is a retained/external
skip. Thus a retained resource does not bypass those admission and lookup steps.
The plan helper checks matching zone, allowed resource hostname and a cpk- tunnel
name; it does not establish account-level remote ownership. For non-EPHEMERAL
lifecycle it returns one SKIP_RETAINED_OR_EXTERNAL action. The adapter then returns
success with the plan descriptor without changing status or calling teardown.

For ephemeral removal, the adapter first commits REMOVING and replaces the resource's
source_run_id with the current run. It passes that updated resource to teardown
outside a unit of work. If teardown raises, a separate transaction marks UNCERTAIN,
then the adapter returns ingress.remove-uncertain with only the exception class.
If teardown succeeds, the clock is validated afterward. A clock failure returns the
same uncertainty code without another database unit of work: the record remains
REMOVING with no removal timestamp/run fields. A valid timestamp permits a final
transaction marking REMOVED, followed by success with the plan descriptor.

Removal preflight maps SecretProviderRegistrationError to a fixed authorization
failure and selected KeyError/ValueError/InvalidOperationCommand exceptions to
ingress.remove-unsupported using str(error). Initial mark-removing, mark-uncertain,
final mark-removed and final evidence failures are not enclosed by a general
outcome-producing handler. This owner does not implement adoption, reconciliation,
automatic retry or recovery of a REMOVING/UNCERTAIN record. It also makes no explicit
reference-registration revocation or generated-evidence deletion call. Any secret
cleanup inside provider teardown needs its own provider review.

The provider-failure projection admits a non-class frozen dataclass instance with
exactly six ordered fields: stage, category, mutation_certainty, tunnel_id,
dns_record_id and cleanup_result. Its four vocabulary fields must be Enum instances
whose string values occur in the owner's closed sets: nine stages, seven categories,
four mutation certainties and four cleanup results. Matching foreign enum/dataclass
types are accepted structurally. Resource IDs may be None; otherwise they must match
ASCII [a-zA-Z0-9._:-]{1,128} and omit token, secret, password and key substrings
case-insensitively. One malformed field discards the entire optional projection.
Attribute/validation exceptions also discard it.

Accepted projection evidence contains provider kind, the four closed values and
zero to two resource entries ordered tunnel then DNS. A none mutation certainty or
complete cleanup result still returns an UNCERTAIN outcome; these fields do not
authorize retry or cleanup. Their syntactic filter is not proof that every permitted
identifier is nonsecret. Success and compensation-coordinate evidence do not pass
through this provider-failure filter.

[BoundedEvidence and FailureEvidence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
constrain JSON size, depth, item counts, text lengths and selected secret-shaped key
names. String values are not universally scrubbed for secrets. Allocation/provider
uncertainty uses fixed messages and class names, but selected preflight errors use
str(error); a too-long or control-bearing message can fail FailureEvidence
construction instead of returning an outcome. Reference-only custody and selected
redaction tests should not be described as universal redaction of all paths.

The complete 1,158-line
[governing test file](../../../../../control-plane-kit-operations/tests/test_ingress_realization.py)
contains 13 tests using real PostgreSQL stores and a tracking unit-of-work wrapper,
but recording provider and secret-authorizer doubles. Its fixture installs schema,
truncates workspaces CASCADE and registers synthetic authority/provider values;
request/plan/run/intent context objects are assembled in memory. It does not run a
real coordinator, Cloudflare API, DNS/tunnel lifecycle or secret-byte custody flow.
The suite was read, not executed for this documentation change.

The tests establish allocation success/reference recording and selected redaction
canaries; 28 positive structural-provider cases across the vocabulary/resource
shapes and 15 hostile cases; compensation for mismatched receipts and invalid
coordinates; pre-create timestamp rejection; provider revocation between create and
recording; compensation failure evidence; distinct allocation names across runs;
successful removal; provider failure with durable UNCERTAIN; and post-teardown clock
failure leaving REMOVING. Provider callbacks observe zero tracked active units of
work. The positive provider cases are not a Cartesian-product matrix.

The revocation callback is sequential, not a concurrency test. The distinct-run
test directly marks the first record removed before allocating again; it does not
prove a same-effect retry is idempotent or exercise actual teardown for that first
record. The tracking commit counter counts commit requests; fake providers and
selected database queries do not prove remote cleanup or recovery after an
ambiguous commit. Retained/external skip, broad grant mismatch, unsupported operation,
concurrent allocation, status-store failures and post-commit evidence failures lack
direct cases in this file.

Reading depth for this companion is the complete owner and governing test, the
canonical timestamp helper and previously read Postgres unit of work, plus selected
contract-bearing paths in coordinator, ingress values/stores, secret helpers/stores,
Core secrets, evidence validation and root exports. It is not a complete audit of
those dependencies or external interpreters. No source or test was changed, no
imports/tests/provider calls were executed, and documentation does not resolve an
existing uncertain deployment or authorize live effects. The test companion remains
a separate review slice.
