Source: [control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py).
Maintain this document alongside its source file. Recheck request identity,
recipient/grant admission, descriptors, mutable evidence and consuming boundaries
when changing the runtime-effect language.

This 1241-line module packages activity syntax and selected runtime material into
a request and represents interpreter outcomes as values. It also owns image-pull
scope values and gateway target maps. It does not dispatch effects, contact
providers, register products, authorize an actor, persist attempts or advance a
graph. The activity operation remains explicit closed syntax inside the request;
an external interpreter selects what it can execute.

## Dependency and ownership map

The module imports environment binding values/codecs, EffectResultKind, RunId,
activity operations and their descriptor interpreter, endpoint observations,
product values/codecs, secret references/grants, Protocol/RuntimeKind and
verification completion/outcome values. These are actual dependencies, not
duplicated implementations. The root Core facade exposes selected values here.

RuntimeAuthorityReference, access-delivery kinds/values/codecs, delivery secret
references, their normalization helper and RuntimeEffectContractError are owned
by [runtime_authority.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py)
and imported here. Names reachable through this module do not acquire a second
owner. That authority language distinguishes named authority, connection material
and process delivery; a name alone establishes neither registration nor access.

The [planning codec](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py)
supplies activity-operation descriptors, while products owns socket ordering and
product/reference admission. Operations consumes these values through its own
durable services and external interpreter protocols. There are no imports of
Docker, HTTP clients, interpreter packages, Operations stores or cpk-server
process code in this owner.

## Image scope, source coordinates and product material

ImagePullAuthority holds registry, optional repository scope and a credential
reference. Registry grammar admits lowercase DNS-like components with an optional
one-to-five-digit port; this helper does not impose an explicit total registry
length or validate the numeric port range. Repository scope is at most 255
characters of slash-separated lowercase OCI-like components. A string credential
is converted to CredentialReference, otherwise the typed reference is required.
permits requires an OCI image, exact registry and either unrestricted repository,
exact repository or a slash-delimited descendant. It does not authenticate to a
registry, resolve credentials or check an image's existence. The codec requires
the exact three fields and mapping/text shapes; nested reference construction
errors are not uniformly wrapped by every entry point.

RuntimeEffectSource carries workspace/request/run/plan/base-graph/desired-graph/
intent-event identities. RunId requires the exact imported value type. Other
identities pass _required_text: nonblank text, at most 512 characters and no
password=/token=/secret= marker, without stripping or canonicalizing the supplied
text. This helper does not generally reject embedded controls or URLs. The source
codec requires all seven exact keys; malformed RunId text becomes a local error
after the caught ValueError handler has exited. No record is looked up or proven
committed by constructing these coordinates.

RuntimeProductMaterial holds node/runtime IDs, ProductReference, a
ContainerServerProduct, public/socket environments, optional pull authority and
process-authority deliveries. It checks typed product/reference values and equal
product identities. It does not recompute the reference digest from the embedded
product, query a registration or call pull_authority.permits on that product.
That distinction matters: the selected Operations consumer can retain a registered
base reference while constructing graph-specific verification/secret-delivery
material. A synthetic matching identity is not registered product evidence.

Public and socket environment collections are sorted and checked independently
for their respective binding types and unique names. The constructor does not
check overlap between the two collections or with secret delivery slots. Its
normalization of process deliveries delegates to runtime_authority's typed-tuple,
unique-authority rule. Other collections here can be sorted before element type
checks, so malformed caller objects can fail during ordering/attribute access
rather than always producing RuntimeEffectContractError.

The product-material descriptor includes product/reference descriptors, both
environment lists and null-or-pull authority. Process deliveries are included only
when nonempty. Its decoder requires the base exact fields and permits the extra
delivery key only when present; absent and empty delivery lists normalize to the
same empty value. Environment decoder lists are capped at 32 bindings each,
whereas direct construction does not apply that count cap. Decoding invokes the
owning environment/product/reference codecs and validates binding kinds; selected
ValueErrors retain causes. This is a mapping codec, not a canonical JSON/digest
or universal diagnostic boundary.

## Executable request admission

RuntimeEffectKind currently has only REALIZE_ACTIVITY. RuntimeEffectRequest
requires a typed source, closed effect/runtime kinds and ActivityId. Its effect_id
and source.intent_event_id must be exact strings with equal values; the request
also rejects NUL and surrogate characters in that identity. This stronger check
occurs before grant validation. ReviewChange is explicitly rejected, and other
operations must be accepted by activity_operation_descriptor. A broad catch wraps
errors from that descriptor with a generic message and retained cause. Being
accepted by the activity codec does not mean every runtime interpreter implements
that operation or that executing it is authorized.

The request sorts authority deliveries, requires typed values and unique authority
references, and requires each to match the optional request authority. Deliveries
without a request authority are rejected. Grants are sorted by reference, intent
and authorization ID, required to be SecretResolutionGrant, and unique by
reference/intent. Each must match source.workspace_id; a non-None grant.effect_id
must match request.effect_id. This constructor does not require a grant effect ID,
match run/activity/actor/correlation, establish all required uses, reject every
unrelated use or consult committed/revoked authorization. Those stronger checks
belong to the relevant consumer. Products are sorted by node ID, typed and unique
by node ID. No aggregate product/grant/delivery count or request byte cap is
implemented here, and sorting precedes several type checks.

The final _validate_runtime_authority_recipient rule is shared with pre-start
intent construction:

- For operations other than StartNode/ReconcileNode, nonempty request deliveries
  are rejected; the helper then returns without comparing product declarations.
- For StartNode/ReconcileNode with no request deliveries and no product delivery
  declarations, it returns without imposing a target-material cardinality rule.
- Once either side declares delivery for those two operation kinds, exactly one
  product must match the operation target node, request deliveries must equal
  that product's declarations, and every delivery must match the request authority.

This binds process access to a selected recipient when relevant. It does not
validate every product-to-operation relation, runtime placement, registration,
provider kind, TLS completeness or actual mount. The private helper is an actual
shared dependency of the adjacent intent language; changing it changes both
admission surfaces.

The request descriptor retains effect/source/activity/operation, runtime kind,
authority, deliveries and products. secret_resolution_grants is intentionally
repr-hidden and omitted from the descriptor. A request descriptor therefore
does not reconstruct transient grants; other retained material can still include
private addresses, configuration content and secret reference identities. This
owner supplies no request decoder or execution method.

## Gateway target language

GatewayTarget is HTTP or PostgreSQL. GatewayTargetId has exactly two dot-separated
lowercase-leading components, each at most 63 characters, and constructors bind
that value to node_id.provider_socket. HTTP targets require HTTP protocol, an
HTTP(S) URL with authority, no userinfo/query/fragment, and at most 512 characters.
The URL helper does not resolve a host, enforce private routing, validate a parsed
numeric port or canonicalize the path. The runtime-private description is not
an access-control or network-reachability check.

PostgreSQL targets require POSTGRES protocol, a lowercase DNS-like host of at
most 512 characters, exact-int port 1..65535, optional bounded identity-shaped
database/username and an optional uppercase environment name for a password.
There is no password-value field. Both target forms validate source-edge strings,
reject duplicates and sort at most 32 entries, each nonempty and at most 512 characters;
the strings are not checked against graph edges or a topology store.

GatewayTargetMap sorts by target ID, limits targets to 32 and rejects unknown
types/duplicate IDs. Sorting occurs before type validation. Its codec requires
the exact outer mapping and target list, exact per-kind fields and an exact
expected Protocol decoded by its owner. Target/source-edge lists and mappings
are representation data; they do not expose a gateway route or probe a target.
The gateway text filter rejects selected assignment, secret-reference and
private-key markers, not every possible sensitive value. Descriptors deliberately
retain URLs, hosts, database/user names and edge IDs.

## Results, failures and evidence limits

RuntimeEffectResult admits SUCCEEDED, FAILED, UNSUPPORTED and UNCERTAIN from the
larger EffectResultKind enum, rejecting IN_FLIGHT and LIMITED_PROGRESS. Class
factories construct those four cases. Success forbids a failure value; every
other case requires typed RuntimeEffectFailure. The exact tuple of observations
may contain exact RuntimeEndpointObservation or VerificationCompleted values.
A successful result may not contain a VerificationCompleted outcome other than
PASSED. Observation order is preserved. The constructor does not correlate
observations with a request's graph/subject or prove their provider origin; no
observation-count bound or whole-result byte limit is applied here.

RuntimeEffectFailure validates code with _required_text and message with
_bounded_text, then copies its details through the evidence helper. Bounded text
allows empty strings, rejects NUL, limits length to 512 characters and applies
the three assignment markers. Required text rejects blank strings but lacks the
same general NUL restriction. Evidence keys must be nonempty strings at most
512 characters with that marker filter; they have no independent NUL check.

Evidence is recursively copied into sorted dictionaries and lists, with at most
32 fields per mapping and 32 items per list. Values begin at depth zero; recursive
list/mapping contents increment depth, and depth greater than four is rejected.
Supported leaves are None, exact bool/int/float or string. Floats are not checked
for finiteness and integers are not magnitude-bounded here. There is no aggregate
encoded-byte cap. These are local structural/text limits, not a proof of RFC8785
serializability, public-safe content or bounded total output at every consumer.

Copies sever the original input containers, but stored nested dictionaries/lists
remain mutable despite frozen dataclasses. Result/failure descriptors shallow-copy
their outer mapping, retaining nested containers. Descriptor calls do not repeat
the constructor's recursive validation after mutation. Secret detection is a
finite substring check, not a universal sanitizer; other addresses, reference
strings or sensitive text without those markers can remain represented.
This module supplies no result/failure decoder, immutable evidence store or
durable outcome acceptance.

## Request, intent and consumer boundaries

The adjacent [observation language](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
projects a request to pre-start intent by omitting generated event identity and
transient grants. Its builder requires an explicit effect/event identity and
explicit grants (default empty), then constructs this request. For admitted
values, the [shared law](../../../../architecture/runtime-effect-request-intent-boundary.md)
is P(B(intent, event, grants)) = intent. The reverse requires the omitted inputs;
equal intent does not prove equal grants, permission or actual provider bytes.

That owner also applies stronger observation-grant checks and a separate
live-result fingerprint path with nominal/semantic revalidation, canonical JSON
and an aggregate byte cap. Do not transfer those guarantees to merely constructing
a RuntimeEffectResult here, or transfer the narrower observation-evidence filter
to arbitrary live-result evidence. The result fingerprint depends on current
descriptor content, including observation order and any retained mutable evidence.

Selected [Operations translation](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
builds intent from pinned realization context, material graph and registered
products, then binds the actual intent event with initially empty grants. It
constructs product material from registered reference plus graph-specific
verification and secret deliveries. Its secret-use enumeration includes value
deliveries, pull credentials, PostgreSQL verification authentication and remote
Docker TLS connection material; it does not arise from this request constructor.

The selected [coordinator dispatcher](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
checks exact context/request and matching source/event/activity/operation, selects
an interpreter and active authority, requests exact secret-use authorizations and
checks returned grant workspace/effect/reference/intent before replacing grants.
Interpreter exceptions, wrong result type or wrong effect ID become an uncertain
result. A later selected coordinator path binds the original attempt event,
dispatches, wraps the result as ExecutionEffectOutcome and hands it to the fold
service. These are consumer responsibilities; a local SUCCEEDED value does not
write an event, resolve an ambiguous attempt or advance a graph by itself.

At external interpreter coordinate d36788e24dcf0c43f49c398d9e01ab02ad453f02, the
selected [Docker dispatch entry points](https://github.com/OpenJ92/control-plane-kit-interpreters/blob/d36788e24dcf0c43f49c398d9e01ab02ad453f02/src/control_plane_kit_interpreters/docker/runtime.py)
check effect/runtime kinds, match supported activity constructors and invoke
concrete methods. Unsupported forms return UNSUPPORTED; selected preconditions
return FAILED; other caught exceptions become UNCERTAIN. The authority entry
point composes an authority-specific client and handles its required close.
This was a source read at that exact commit, not an installed-Core compatibility
claim, full Docker-method review or executed provider evidence.

## Tests and maintenance evidence

The full [953-line suite](../../../../../control-plane-kit-core/tests/test_runtime_effects.py)
and its [companion](../../tests/test_runtime_effects.py.md) cover twenty-five tests
with five helpers: named authority and delivery descriptors, request/product
material, selected grants, socket permutation/equality/inverse laws, image scope,
synthetic result observations, gateway mappings and selected negatives.
The grant-effect negative at test lines 407–446 fails at the earlier request/
source identity check, so it does not independently protect the later grant guard.
Synthetic descriptor/image digests and grant records do not establish provenance.

Selected [observation-boundary assertions](../../../../../control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py)
separately protect hidden/omitted grants, request/event identity and fingerprint
revalidation, including forged malformed result cases. Those are additional
consumer laws; this authoring did not reread that entire suite. No executable
validation was run. Authoring read all 1241 owner lines across test/owner review,
the full primary suite/helpers, imported authority/identity and selected product,
planning, observation, verification, Operations and external consumer bodies.
Only this owner gains coverage; those consumer reads are scoped, not full audits.

Security and operational history: no code, runtime, network, auth, secret,
provider or mutation behavior changed. This owner creates no transactions,
attempt history, retry/cleanup decision or authoritative observation. Descriptor
filters and matching values do not grant permission. Unknown-key diagnostics and
selected nested errors may retain field names or causes; consumers must enforce
their own disclosure and bounded-output contracts. Links, whitespace and frozen
source/test consistency were checked. No application imports, executable tests,
database/provider calls, source changes or merge occurred during authoring.
