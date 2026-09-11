Source: [control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This service prepares durable node-control intent in one caller-owned transaction:
check trusted scopes and accepted graph declarations, select distinct signing
authorities, authorize each private-key reference and retain one exact unsigned
attempt. It returns two deferred signing requests after transaction exit. It does
not resolve keys, sign grants, relay a command or record execution success. A row
and preparation value mean INTENDED, not delivered or applied.

RequestNodeControlIntent is a frozen, slotted value containing TrustedCommandContext,
a node-role gateway reference and a NodeControlCommandRequest, checked with
isinstance. The actual Core
[identity contract](../../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py)
requires context scopes to equal the principal's workspace grant and derives actor
from principal subject. This service consumes that trusted value; it does not
authenticate credentials or make arbitrary request-body claims trustworthy. The
outer process boundary must establish the principal/context before calling it.

execute requires four scopes before entering a UoW: NODE_CONTROL_READ for read-state
or NODE_CONTROL_APPLY otherwise, plus NODE_CONTROL_EXECUTE, DELEGATION_KEY_USE and
SECRET_PROVIDER_USE. These are required membership checks, not a requirement that
the supplied scope tuple contain no additional grants. Read and apply scopes do
not substitute for each other. The execute scope authorizes this preparation path;
it does not mean the service crosses an effect boundary or bypasses later checks.

Before durable reads, the service computes the
[attempt fingerprint](node_control_attempts.py.md) from actor, gateway node and
canonical request digest. In one UoW it locks workspace/request identity and reads
an existing attempt through the actual
[attempt store](postgres/node_control_attempt_store.py.md). If its fingerprint
differs, preparation conflicts. If equal, it requests commit and returns that same
retained evidence with replayed=True. Scopes are still checked, but this branch
does not call the epoch/wall clocks or ID factory, reload current graph/projection,
reselect active keys or reauthorize secret uses. Store reconstruction still checks
retained bytes and selected joined key/authorization witnesses. Replay is not an
unconditional cached object return or fresh signing permission.

That distinction is deliberate: a retained attempt can replay after current
workspace pointers are cleared or its keys become revoked, without replacing its
grants. The later
[signing-authority reload](node_control_signing_authority.py.md) performs current
lineage, active authority, provenance and grant-time checks. Equal semantic replay
does not renew validity or establish that a command has not already been delivered.

Fresh preparation first requires the request workspace to equal the context
workspace, locks the workspace row and requires its accepted current authored
graph ID to equal the request graph revision. It loads the current realized
projection, decodes it through DEFAULT_GRAPH_CODEC and checks projection workspace
and source-authored lineage. The selected actual
[graph store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
returns a typed projection record; this path reads the realized descriptor, not
the authored source graph or desired pointers. It does not query runtime health,
addresses or a live gateway. The retained runtime ID comes from the selected
gateway node's graph declaration.

Graph authorization requires the named gateway's control provider socket and the
target node/provider socket to exist, both HTTP and on equal runtime IDs. It then
requires an HTTP edge whose provider role/socket equals the requested target.
Precisely, this scan ranges over all graph edges and does not compare consumer_role
or requirement_socket with the selected gateway. The inspected fixture has one
gateway-to-target edge; it does not exercise another consumer or multiple gateways.
This is the source's current membership predicate, not proof that every matching
edge belongs to the selected gateway's map.

The target block must declare a control surface on the requested provider socket,
the named variable and an operation contract whose command codec is exactly the
request's codec. Missing declarations or selected operation lookup failures become
NotFound. This owner uses explicit sockets/surfaces/contracts; it does not inspect
product metadata or infer permission from a block class name. It also does not
compare the command's precondition to a live variable state. Core descriptor and
request constructors enforce their own internal laws before this service can use
them; runtime interpretation must enforce its own transition contract later.

Next it selects one active key for each purpose, transit before workload. The
actual [key store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
takes a shared workspace-purpose advisory transaction lock, selects at most two
active rows across issuers and requires exactly one. Its row decoder checks stored
public fingerprint against public material. _select_key translates selected
lookup/conflict/ValueError failures and requires the returned purpose; it is not
an independent reimplementation of every registered-key invariant. Fresh service
preparation does not itself recompute the key registration ID from all material.

The two authorities must differ in registration ID, public fingerprint and private
key reference. Reusing any of these conflicts. This prevents the selected dual-use
substitutions but does not prove cryptographic correspondence between a public key
and secret bytes, since no private material is resolved. Lifecycle coordination
relies on writers taking the key store's opposing purpose lock; arbitrary SQL
writers do not acquire that advisory lock automatically.

The constructor accepts a UoW factory, epoch clock, canonical timestamp clock and
ID factory, with grant_lifetime_seconds defaulting to 60 and requiring an exact
integer in 1..300. Only the fresh path calls the clocks and consumes three IDs in
order: attempt, transit JTI, workload JTI. Both grants receive the same issued_at
and not_before and expiry issued_at+lifetime. Core constructors validate those
grant values; ordinary construction/callback failures inside that block become a
fixed unsigned-grant error. This owner does not compare the wall timestamp with
the epoch clock. Transit binds gateway/attempt and request coordinates; workload
uses workload_node_control_audience(request.target). Both are unsigned data.

For each key, the service derives a deterministic secret-use correlation from
workspace, private reference, family intent, actor and attempt operation_id, with
other optional provenance absent. The actual
[secret-use helper](secret_providers.py.md) checks SECRET_PROVIDER_USE and timestamp,
locks correlation, then locks the active reference and exact active provider for
update. It reruns workspace/provider identity, reference-prefix and allowed-intent
policy, builds deterministic authorization evidence and either accepts matching
correlation replay or inserts it. The selected
[secret stores](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
retain those row locks in the same connection. This helper never commits and
returns authorized reference evidence plus provider; this owner does not return
a SecretResolutionGrant or provider endpoint/credential references.

Transit authorization precedes workload authorization. After both succeed, fresh
preparation constructs NodeControlIntendedAttempt with current lineage, gateway
runtime, both key/authorization/correlation IDs, original request and grants, then
inserts it and requests commit. The actual
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only on successful requested exit; otherwise it rolls back and closes.
The two authorization inserts and attempt insert therefore share one transaction.
Failure of the second authorization must not leave the first committed. There is
no external-effect compensation to perform here. Generated in-memory IDs and
clock observations are not rolled back.

After UoW exit, _preparation constructs the return value and two distinct frozen,
slotted deferred family wrappers. Each wrapper validates registration/authorization
ID grammar and its grant family using isinstance, hiding IDs from repr. The outer
preparation validates bool replayed and checks that both wrappers exactly agree
with the retained attempt's IDs and grants. Direct construction of these values
does not prove a commit or current authority; those guarantees depend on the service
and subsequent reload. Returned wrapper construction happens after the transaction,
so failure there would not undo an already committed intent.

Errors distinguish denied scope, missing graph declaration, conflicting current/
authority/replay truth and other intent failure. Expected lookup and selected
grant/policy errors become bounded fixed messages raised outside their handlers.
The catch sets are local: for example correlation derivation occurs before the
secret-authorization try block, attempt construction occurs outside the insert
error translation, and unexpected database/UoW failures can propagate. This is not
universal exception/log sanitization. Deferred IDs are repr-hidden, but request,
actor, lineage and unsigned grants remain operational data in the preparation.
The service has no public log, event emitter or durable delivery-result projection.

Selected actual
[tests](../../../../../control-plane-kit-operations/tests/test_node_control_intents.py)
use real PostgreSQL UoWs and a constructed principal/context, a realized graph with
control surface/runtime/edge values, actual provider/reference admission and
directly inserted keys with derived registration IDs and synthetic public material.
The authored fixture deliberately lacks runtime truth, demonstrating use of the
realized projection. Happy-path assertions require transaction exit before return,
two authorized secret uses, one attempt and coherent deferred grants; the tracker
counts commit requests around a real UoW, not an independent database commit hook.

Selected negative cases remove each required scope, substitute unrelated scopes,
alter workspace/graph/target declarations and mutate signing authority. Some
descriptor-invalid cases fail in Core construction before service invocation.
Replay tests forbid clock/ID calls and clear current pointers/revoke keys before
requiring identical returned evidence. A revoked workload reference checks rollback
to zero authorization/attempt rows. A two-worker service test requires one fresh
result, one replay, identical attempts and only one attempt/two authorization rows;
it tests concurrent preparation, not duplicate command delivery. A separate helper
test probes reference/provider locks during authorization and their release after
forced rollback, not through a completed service signing/dispatch operation.

The selected AST test excludes forbidden framework/effect module prefixes and
product/metadata imports, checks selected identifiers and lacks signer/dispatcher/
resolver/relay/client constructor parameters. It is source-boundary evidence, not
runtime instrumentation of arbitrary injected callbacks. Detailed lifecycle/key-
generation concurrency tests elsewhere in the same file were not read for this
owner note and are not credited here; the test companion remains a separate batch.

Read depth: full 637-line owner; selected test ranges 1..866, 1058..1487 and
1538..1642, including consequential fixtures/helpers, with retained full attempt,
store, signing-authority and UoW context. Selected actual Core identity, graph/key
selectors and secret authorization/correlation/policy/store dependencies were read.
The all-edges target membership qualification above is source-derived; no new
multi-gateway test or live counterexample was executed. No source/pin changes,
executable tests, database setup, credential/private-key access, provider/runtime
actions or publication occurred. Documentation introduces no new security surface;
the trusted caller, transaction and later effect boundaries remain explicit.
