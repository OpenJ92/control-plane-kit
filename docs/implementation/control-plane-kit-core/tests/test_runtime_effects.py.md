Source: [control-plane-kit-core/tests/test_runtime_effects.py](../../../../control-plane-kit-core/tests/test_runtime_effects.py).
Maintain this document alongside its source file. Recheck constructor ordering,
descriptor assertions and imported owner boundaries when changing runtime effects.

This 953-line suite has twenty-five tests in one class and five local helpers.
It constructs runtime authority, request, product, result and gateway values.
There is no Docker start, registry pull, socket mount, TLS connection, provider
resolution, HTTP/PostgreSQL probe or durable execution in these tests.

## Helpers and imported owners

_run_id dynamically imports operations.run_identity and constructs RunId. It
converts only ModuleNotFoundError naming that exact module into the historical
missing-#1636 assertion; other import failures propagate. This is a test helper,
not a production fallback implementation. _source constructs workspace/request/
run/plan/base-graph/desired-graph identities and intent_event_id=effect-a.

_product_material fixes node api, runtime docker, openj92/hello-server/1 identity,
a synthetic descriptor digest of 64 b characters, public HELLO_MESSAGE and
socket-derived UPSTREAM_URL with edge provenance. _product supplies a ghcr.io
image with synthetic sha256 plus 64 a characters. By default it declares one
HTTP provider on port 8000; when sockets are supplied it uses those sockets and
an empty provider-port tuple. _runtime_product_sockets independently reverses
two named HTTP requirements and two named HTTP providers.

The [runtime-effect owner](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
imports and exposes authority types and the shared exception from the actual
[runtime-authority owner](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py).
The [product contract](../../../../control-plane-kit-core/src/control_plane_kit_core/products.py)
normalizes requirement/provider ordering at ProductRuntimeContract construction;
plain BlockSockets does not perform that normalization. Environment binding,
secret grant, RunId and endpoint observation admission come from their respective
Core owners. A test import through runtime_effects is not an ownership transfer.

## Authority reference and delivery assertions: six tests

The reference positive fixes the exact one-field mac-mini-docker mapping and
mapping round trip, and excludes docker.sock from benign descriptor repr.
Twelve negatives cover empty/mixed-case/spaced/path-shaped names, TCP URL, socket
path, credential JSON, password=/token=/secret= assignments, a private-key marker
and dockerconfigjson. An extra endpoint descriptor key must report unknown keys.
The actual owner combines identifier grammar with a finite substring filter;
these selected cases are not universal secret detection or endpoint authorization.

Local-socket delivery fixes the exact nested authority/kind/empty-reference-list
descriptor and round trip and excludes socket/transport text from benign repr.
The remote-TLS case supplies ca-cert, client-cert and client-key references,
checks that ordered label tuple and round trip, and excludes BEGIN/PRIVATE KEY
from repr. Input labels are already sorted. It proves reference representation,
not sorting under permutation, PEM validity, resolution or a TLS handshake.
Nor does it establish that the delivery constructor requires exactly those
three TLS labels: the owner sorts typed references, checks label uniqueness and
forbids references on local-socket delivery, without that TLS completeness rule.

Delivery negatives reject three extra material keys (host_path, endpoint, token),
an unknown ambient-env kind, an extra nested target_path, and a local-socket
delivery carrying a secret reference. The label/reference codec test fixes its
exact two-field mapping and round trip and rejects label token. No cloud-session
delivery behavior, duplicate-label negative or actual mount is exercised here.

## Request material and selected admission: seven tests

The main descriptor test fixes realize-activity/docker, absent authority,
empty authority deliveries, all seven source fields and the StartNode(NodeTarget
api) operation mapping. It checks product node/runtime identifiers, public and
socket environment descriptors and the synthetic image digest. Decoding that
product mapping must preserve each environment tuple; it is not a full request
round trip or verification of the descriptor/image digest against external bytes.

The explicit-delivery positive supplies the same local-docker delivery on the
request and its target product and fixes the request delivery descriptor, with
socket/unix text absent from benign repr. Two negatives omit the request authority
or use other-docker. The actual request rejects these before its later recipient
rule. They do not independently test missing/wrong target material, wrong node,
multiple products or product/request delivery disagreement. A separate request
test fixes only the mac-mini-docker authority descriptor and excludes TCP/socket/
token text, without products or authority-access delivery.

The secret-grant positive directly constructs reference-only grant material,
including workspace-a and effect-a, and checks that the request retains its
one-item grant tuple. It does not consult committed authorization despite the
test name's committed wording. The negative changes request.effect_id to
other-effect while _source still supplies intent_event_id=effect-a. The actual
constructor rejects that source/event identity mismatch before inspecting grants.
Consequently this negative does not isolate the optional grant effect-ID check.
There is no separate wrong-workspace or wrong-grant-effect case that preserves
valid request/source identity. The source does contain those grant guards, but
this assertion does not independently protect them.

The duplicate-grant negative supplies the same grant twice and requires a unique
error. The owner deduplicates by reference/intent; this test does not vary
authorization ID while retaining the same use. Another test rejects a raw string
instead of RuntimeEffectKind and rejects ReviewChange as executable work, checking
the respective message fragments. None of these tests proves approval, exact
coverage of every required secret use or provider permission. The request owner's
descriptor omits transient secret grants; this suite does not directly assert
that omission. The shared [request/intent relation](../../../architecture/runtime-effect-request-intent-boundary.md)
explains why omitted execution inputs cannot be recovered from retained intent.

## Product equality and environment admission: three tests

One negative pairs a router ProductReference identity with a hello-server product.
The constructor checks identity equality, not that the supplied synthetic
descriptor digest hashes the embedded product. No digest-mismatch negative or
registration lookup occurs here.

The permutation test compares requirements-only reversal, providers-only reversal
and both reversals against canonical material. Each subcase checks descriptor
equality, re-encoded restored descriptor equality, material equality and exact
decode/encode inverse equality. This exercises two sockets in each direction,
with normalization owned by ProductRuntimeContract. It is stronger than checking
only encoded ordering, but not a universal permutation proof or a raw canonical
JSON byte/hash oracle.

Four environment negatives supply the wrong binding type in each field and
duplicate names with different values in each field. They check each collection
separately; they do not test overlap between public and socket collections or
with secret delivery names. The actual RuntimeProductMaterial constructor sorts
and validates each environment collection independently. Cross-source composition
claims need the corresponding owner and tests.

## Image-pull authority: three tests

The positive fixes registry, repository prefix and credential-reference mapping
and round trip. It permits one child repository in that registry and rejects
an image that changes both registry and repository. That negative does not
isolate repository rejection within a matching registry. The source predicate
requires matching registry and either no repository restriction, exact repository
or a slash-delimited descendant; the test does not cover all three branches.

The negative codec case rejects an extra token key; construction rejects a raw
ghp_ credential string. A RuntimeProductMaterial case pins the credential URI in
the descriptor, excludes token= from benign repr and checks full material mapping
round-trip equality. No credential is retrieved, OCI image fetched, registry
permission tested or installed digest verified.

## Result and gateway representation: six tests

The successful-result test constructs container-name evidence and a synthetic
runtime-private HTTP observation at http://api:8000. It fixes SUCCEEDED and the
full one-observation descriptor, including graph/subject/socket/protocol/context
and literal address. It does not assert the evidence mapping itself or establish
that a container or reachable endpoint exists. The selected
[observation owner](../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py)
validates the typed observation and representation; construction is not a probe.
The other result test rejects token= in evidence and password= in a failure
message. It does not cover all result kinds, failure/result consistency,
verification outcomes, depth/width/text limits, nonfinite numbers or diagnostic
redaction. The actual evidence filter rejects a finite set of assignment markers.

The gateway positive fixes the complete two-target HTTP/PostgreSQL mapping,
including target IDs, nodes, provider sockets, protocol mappings, addresses,
database/user/password environment slot and source edges, then checks round trip.
Input targets are already in sorted target-ID order; no permutation is supplied.
The benign repr exclusion for cpk-local-gateway does not establish network
isolation or remove the actual private addresses intentionally in the descriptor.

Three gateway negative tests reject a duplicated target ID with a different URL,
a redis protocol descriptor substituted into an HTTP target, and selected
credential/secret-shaped material: HTTP userinfo, a password= host, an extra
password field in a PostgreSQL descriptor and a malformed password environment
name. The latter is rejected by environment-name grammar before secret filtering.
These do not exhaust target-ID agreement, list/field limits, ports, source-edge
duplicates, URL parsing or network exposure. No HTTP request or database query
occurs, and the password environment name is not a password value.

## Evidence and review limits

Negative cases assert RuntimeEffectContractError, sometimes with a message
fragment. They do not check complete messages, sizes, cause/context chains or
that supplied canary values are absent from exceptions. Some unknown-key helpers
include field names in diagnostics; selected nested decoder errors retain causes.
The positive absent-word checks use benign constructed metadata. Treat these
as specific examples, not blanket secret-free/error-safe evidence.

The source has explicit bounds and additional admission laws beyond those
assertions. Test names such as committed, secret-free and exact must be read
against the actual assertion and the first constructor guard reached. The suite
also does not run an import-isolation check just because a test says without
Docker. Maintaining these tests should preserve the pure request/result and
representation boundary while leaving effect execution, durable provenance,
provider truth and retry decisions with their actual owners.

Authoring read all 953 lines, twenty-five tests and all five helpers, actual
request/product/result/gateway/image-pull constructors, descriptors and selected
private guards, imported authority codecs/guards, RunId and selected product/
socket/environment/observation contracts. Reviewed secrets and request/intent
context was retained. This is not a full new review of the 1241-line runtime
effects owner or other imported owners. Only this test companion gains coverage.
Security: no runtime, network, auth, secret or mutation behavior changed. Links,
whitespace and frozen source/test consistency were checked. No application
imports, executable tests, database/provider calls, source changes or merge.
