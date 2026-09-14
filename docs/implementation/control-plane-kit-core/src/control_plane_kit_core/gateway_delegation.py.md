Source: [control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Exact probe requests and unsigned delegation claims

The closed request language contains HTTP status and Postgres select-one probes.
Each request names a typed `GatewayTargetId`; it carries no arbitrary origin URL
or SQL text. The imported target constructor requires exactly two lowercase
identifier components, node and socket, each at most 63 characters. It does not
resolve that target against a live registry or establish graph membership.

HTTP requests require a slash-prefixed path capped at 512 characters, without
NUL, backslash, `://` or selected secret-assignment markers. This is not complete
URL/path normalization: double-leading slashes, dot segments, percent encodings,
ordinary query/fragment text and other control characters are not generally
rejected here. Postgres requests require path=None. The receiving interpreter
must preserve target binding and its own transport constraints.

The canonical digest is SHA-256 of sorted, compact, ASCII-escaped JSON for all
three fields: kind, target_id and path, including null. `GatewayProbeRequestDigest`
checks a 64-character lowercase hexadecimal shape. Neither constructing a digest
value nor computing one authenticates a request.

`DelegatedGatewayProbeGrant` is unsigned claim data: issuer/key/audience,
workspace/operation/request/gateway identities, kind/target/digest, issue/expiry
seconds and JTI. References allow bounded mixed-case ASCII text; key IDs have
a narrower 128-character grammar. Both epochs must be actual nonnegative ints,
with expiry strictly later and lifetime at most 300 seconds. Absolute epoch size
is not capped and no clock is sampled. A typed digest is accepted without a
request to recompute against, so kind/target/digest agreement with an actual
request remains a consumer obligation.

Request and grant codecs require mappings with text keys and their exact field
sets, reconstructing the typed values and closed enums. Grants deliberately
contain no signature or compact credential field. Their descriptors still expose
the claim coordinates; unknown-key errors can echo supplied names and wrapped
enum errors retain causes. Grammar and finite path markers are not arbitrary
secret detection or a universal safe-error boundary.

Access-path labels distinguish runtime-private and named-public-ingress transport
without selecting a provider. The canonical health-disclosure factory describes
public minimal liveness, delegated readiness and no public target count. The
policy constructor admits other typed combinations; this module enforces no
HTTP authorization or response policy.

Verification results contain only accepted plus an optional closed rejection
code. Accepted values cannot carry a code and rejected values require one of
five reason classes. `allow()` is a public value constructor, not proof that
signature, time, audience, request or replay verification ran.

Full 444-line owner and full 342-line
[governing test](../../tests/test_gateway_delegation.py.md) read. Selected actual
GatewayTargetId admission and Operations
[grant construction](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_probes.py)
were inspected; the latter selects signing material and computes the digest
from its command's request. This is not a full issuer/verifier/interpreter audit.
Core performs no operator authentication, signing, header parsing, replay
storage, target IO or durable mutation. No executable validation was performed.
