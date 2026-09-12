Source: [control-plane-kit-operations/tests/test_gateway_probes.py](../../../../control-plane-kit-operations/tests/test_gateway_probes.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This suite exercises the [gateway command service](../src/control_plane_kit_operations/gateway_probes.py.md)
with real PostgreSQL persistence and recording substitutes for dispatch and
secret-use authorization. Every test in the class requires
CPK_OPERATIONS_TEST_DATABASE_URL, installs the schema and truncates workspaces
with CASCADE. Even the fake-connection timestamp and AST tests inherit that setup.
The package's Docker test harness supplies the intended disposable database;
these fixtures must not be treated as read-only diagnostics against retained data.

Setup constructs a current graph with a gateway, HTTP application, connector,
registered product descriptors and one named public ingress. Product image
digests, public key material and secret references are fixtures. Signing keys
are inserted directly into SQL; the authorizer constructs synthetic resolution
grants and the dispatcher always returns succeeded/healthy evidence. No gateway,
DNS, ingress, image, private-key custody, signature or live HTTP/Postgres probe
is verified by this file.

The main success test observes zero tracked active transactions during dispatch
and three requested commits including graph seed, intent and result. It checks
the private endpoint's graph/node/socket/protocol/context/address, selected key
reference/public ID, probe-bound authorization and descriptor omission of the
words signature, compact and authorization. This establishes the local service
ordering and selected descriptor fields; it does not inspect an independent
connection during dispatch or prove all possible exception/evidence values are
redacted. The public-path test expects the graph-declared HTTPS address. Three
SQL descriptor perturbations cover missing ingress, wrong target and ambiguity,
restore the original descriptor, and assert no dispatch.

Request replay must preserve the original receipt and call neither dispatcher
nor authorizer again. Changing the HTTP path or access path under the same
request ID conflicts. A fresh service whose clocks and ID factory raise proves
matching replay does not consult them. These cases exercise terminal success
replay; they do not cover interrupted intended receipt recovery, actual process
restart, concurrent same-request callers or gateway JTI replay-cache behavior.

Timestamp cases use an impossible calendar date to require direct add/complete
validation before a fail-on-access connection can execute SQL. Terminal
completion replay still rejects that timestamp and preserves its settled row.
A malformed service clock leaves the attempt count unchanged and dispatch empty.
Native aware timestamp updates plus a Tokyo database session timezone establish
UTC seconds, nonzero microseconds and NULL completion decoding through get,
get_by_request_id and page. They do not convert the grant epoch fields into
wall-clock timestamp ordering; the paging suite owns that separate law.

Authority/current-graph negatives cover insufficient scopes, a stale graph
pointer and an undeclared target before dispatch. The test named
missing_or_ambiguous_active_key only retires the available key and checks the
missing-key branch; it does not seed competing active issuers. Verification
configuration covers one active plus one verify-only key and a renamed gateway
coordinate that need not be resolved in the graph. Synthetic secret denial folds
to rejected without dispatch. HTTP- and MCP-shaped requests call the same local
adapter with an in-memory authenticated principal and check two public-path
dispatches; this is adapter parity, not transport or authentication middleware
acceptance. The AST assertion rejects named absolute Postgres imports in this
owner, rather than proving every possible dynamic or transitive import path.

Read depth: full 1,075-line test owner, full 657-line service and 252-line store,
full sibling [paging suite](test_gateway_probe_read_pages.py.md), selected Core
request/grant, endpoint, evidence, paging and schema contracts. No test was run
for this documentation change. The same-checkout Core dependency used by the
Operations harness is source context, not evidence for an independently pinned
SDK or a published server image. No new security/runtime surface is introduced;
the fixture's destructive setup and simulated external boundaries remain explicit.
