Source: [control-plane-kit-operations/tests/test_node_control_signing_authority.py](../../../../control-plane-kit-operations/tests/test_node_control_signing_authority.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests cover the public signing-authority value contracts and a static
source boundary. They do not call
[NodeControlSigningAuthorityReloadService.execute](../src/control_plane_kit_operations/node_control_signing_authority.py.md),
open a database or use a fake store. They construct typed requests/grants/keys and
inspect Python source, dataclass fields and signatures. The separate PostgreSQL
store/test pair owns distinct validation and is outside this batch. No tests were
executed for this documentation.

The fixture builds a scalar-replacement node-control command for workspace-a,
graph-current, router/control, routing variable and precondition version 4.
Transit and workload grants share target, command identity/digest and times
issued/not-before 100, expiry 200, with separate keys/jtis and transit gateway
identity. Public PEM strings AAAA/BBBB/CCCC are synthetic framed material, not
generated keys or cryptographically verified pairs. Resolution grants use
synthetic provider/credential/secret references and fabricated registration/
authorization IDs. Constructing them does not establish committed authorization
or provider custody. The attempt helper exists but no reload test consumes it.

contract retrieves public names from the Operations root and requires presence.
The algebra test checks exact field lists for selector, shared deferred request,
two family authorities and pair, plus __slots__ and root attribute identity.
Its identity comparison uses the same root lookup; it is not an independent
owner-module re-export comparison. The reload service constructor must expose
only a positional-or-keyword UoW factory and keyword-only epoch_clock. Selected
actor/correlation/fingerprint fields must have repr=False. These are interface
checks, not a successful authority reload or every frozen-value invariant.

The shared-deferred test accepts a coherent pair, then rejects six changes:
attempt mismatch, current authored graph mismatch, either family from another
request/attempt, and each family swapped into the other slot. The next test
rejects malformed actor/correlation/fingerprint anchors using objects, oversized
or slash-bearing text, uppercase digest and short digest. These cover selected
closed-type/boundary cases; they do not enumerate every allowed identifier,
realized-projection drift or every time/request mismatch.

The pair test checks that public keys survive in the returned values while repr
omits public PEM framing, secret://, provider-token and provider-a. Swapping family
authority types fails. Hostile transit-request and shared-deferred subclasses
are rejected at aggregate construction. This tests nominal aggregate boundaries
and chosen repr canaries; it does not inspect generic serialization, signed
tokens, logs or every potentially sensitive identifier in unsigned grant claims.

Six actor/correlation/authorization vectors change each anchor independently for
transit and workload, requiring aggregate rejection. A further case swaps the
two deferred correlations while retaining original authorities. Another ten-case
matrix rejects changed public material under unchanged key IDs for each family,
hostile public-key/resolution-grant subclasses on transit, mismatched operation IDs
on each family, swapped signing intents, transit session provenance and workload
probe provenance. Fingerprints detect the changed public PEM text in these
constructed values; there is no private-key resolution or signature verification.

assert_contract_error requires NodeControlSigningAuthorityError, message length
at most 128, repr length at most 180, omitted selected canaries and no cause or
context. Those checks apply to the constructor factories invoked by the matrices.
They do not exercise unavailable errors from missing/corrupt attempts, stale
workspace/key/provider/reference rows, expired grants, clock failures or database
exceptions. Test names using every describe their selected vectors, not exhaustive
validation of every field in every authority family.

The source-boundary test parses the public module and rejects literal imports
under FastAPI, HTTPX, requests, socket, Docker, JWT and the interpreter package.
Its helper separately detects several absolute and level-one-relative Operations
Postgres imports. Six synthetic import snippets test that detector, and the
actual module must not match. Literal sign(, resolve( and fetchall( substrings
must be absent, and the root must not export NodeControlSigningAuthorityStore.
These assertions keep the public owner away from named implementation imports;
they are not a complete dynamic import/call analysis or proof of no database IO.
Actual reload intentionally calls supplied store methods and takes locks.

The actual [owner](../src/control_plane_kit_operations/node_control_signing_authority.py.md)
adds stronger, separate service guarantees: locked workspace current lineage,
unambiguous active keys/registration identity, shared-locked authorization/reference/
provider chains, reference policy and exact retained provenance, followed by Core
unsigned request/time verification. It returns a pair after UoW exit. None of
that transactional sequence, lock contention, provider revocation/supersession,
clock boundary or post-return authority drift is executed by these contract tests.

Core owns canonical requests, unsigned grant comparison and reference-only
resolution values; Operations owns retained intent and the reload composition;
provider resolution and signing remain external effects. Directly constructing
a coherent pair in this suite does not certify it came from durable truth or
authorize a caller to sign. The static and constructor assertions should remain
distinct from service/store/live evidence when reporting readiness.

Read depth: full 793-line test and 622-line owner; selected actual request/attempt/
deferred/unsigned-verifier, key/provider/reference and UoW/store wiring contracts
were inspected. The separately located PostgreSQL test was only identified for
scope and is not credited as reviewed here. No executable validation, source/pin
changes, credentials/keys, database/provider/runtime actions or publication were
performed for these notes. Documentation adds no security or mutation surface.
