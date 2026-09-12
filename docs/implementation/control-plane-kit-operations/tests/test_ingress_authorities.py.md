Source: [control-plane-kit-operations/tests/test_ingress_authorities.py](../../../../control-plane-kit-operations/tests/test_ingress_authorities.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The pure value class exercises hostname policy, authority/resource descriptors,
teardown actions and generated-token reference plans. The store class uses real
PostgreSQL: it requires CPK_OPERATIONS_TEST_DATABASE_URL, installs schema,
truncates workspaces with CASCADE and seeds two workspaces. These are disposable
database fixtures. Cloudflare IDs, hostnames, secret references and custody
receipts are synthetic values; no provider, DNS, public endpoint or credential
is inspected by these tests.

Exact-host policy round-trip admits one hostname and rejects alternate, nested
and foreign-zone hosts. Negative patterns include zone apex, other zone, excess
depth, uppercase/malformed labels, trailing dot, whole-zone wildcard and duplicate
wildcards. The existing prefixed wildcard still admits its selected host. A raw
API-token string is rejected in a typed SecretReference field, while a hostname
containing the benign word token remains valid. Descriptor assertions check
selected raw-token markers, and resource tests deliberately accept benign
secret-shaped identifier values. These laws do not establish arbitrary-value
redaction or provider token permission.

Resource values keep epoch/status separate from ephemeral/retained/external
ownership policy. Removed status requires removal time/run; active rejects those
fields. Teardown tests assert exact recorded DNS/tunnel IDs and DNS-before-tunnel
ordering, with retained/external skip actions. Missing evidence, wrong zone,
out-of-policy hostname and non-cpk tunnel name reject. The no-search assertion
inspects the pure descriptor; it does not exercise a Cloudflare deletion adapter
or prove the recorded resources are currently owned or safely deletable.

Token-plan tests assert TUNNEL_TOKEN, its reference and Cloudflare intent, plus
allocate/record/start ordering. The connector helper rejects zero or duplicate
matching deliveries. Wrong zone/hostname rejects planning. A constructed custody
receipt projects provider version and source coordinates into reference-only
evidence. The suite does not validate wrong-intent direct plan construction,
provider receipt authenticity/status, live secret custody or graph recipient
selection. Planning the sequence is not execution of it.

Authority persistence checks exact/wildcard policy retention, workspace isolation,
microsecond timestamp, sequential replay and changed-zone replacement conflict.
Malformed admission time gives the fixed canonical-UTC error and zero rows;
the test does not instrument every connection access, although source ordering
places encoding before store lookup. Revocation removes active selection while
keeping the same ID inspectable. A scope case denies registration with PLAN_EXECUTE
and exercises list/detail projection; it does not test a missing revoke scope or
transport authentication. Selected hostname use succeeds or fails via the local
active-policy selector, not a provider call.

Owned-resource persistence checks full receipt replay, changed-tunnel conflict,
workspace lists and source attribution. Marking an epoch removed permits epoch 2
while retaining epoch 1. Uncertain/orphaned fixtures block sequential reentry,
and active-to-removing-to-removed preserves epoch and removal attribution. These
cases do not exercise concurrent MAX+1 allocation, stale status writes, repeated
terminal transition calls or provider cleanup. The resource fixtures can be
recorded without registering the named authority or corresponding source events;
they prove receipt persistence rather than those external relationships.

Generated-secret cases persist/reload by source and list by workspace, replay
identical evidence and reject a changed receipt/reference under the same source.
The activity-ID example uses the allocation-style colon form, but that particular
test only constructs/describes evidence. The run-boundary test persists 200-character
run IDs in resource source/removal and generated-secret records, then attempts
three SQL corruptions using run/bad. It checks exact CheckViolation constraint
names and verifies the original rows survive. Those are disposable corruption
tests, not a repair procedure. New-source reuse of an existing secret reference,
custody metadata corruption and actual provider version access are not covered.

Fresh UoWs establish durable visibility, not a server restart. No real network,
credential delivery or authentication surface is exercised, and no general
concurrency/compensation guarantee follows from these sequential assertions.
Read depth: full 1,097-line owner, full
[941-line language/service](../src/control_plane_kit_operations/ingress_authorities.py.md)
and [845-line store](../src/control_plane_kit_operations/postgres/ingress_authority_store.py.md),
selected actual Core custody/secret-delivery/ingress contracts, run/activity
grammars, current-schema constraints and read projection, with full shared
redactor and previously read UoW/temporal contracts. No tests or database/provider
operations were run for this documentation; fixtures and source remain unchanged.
