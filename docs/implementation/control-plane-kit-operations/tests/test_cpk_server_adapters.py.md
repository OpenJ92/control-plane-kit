Source: [control-plane-kit-operations/tests/test_cpk_server_adapters.py](../../../../control-plane-kit-operations/tests/test_cpk_server_adapters.py).
Maintain this document alongside its source file. When the test or relevant adapter
contracts change, verify and update this companion in the same change.

This 3,704-line suite contains 43 tests: five RunIdentityAdapterContractTests without
a database fixture and 38 CpkServerOperationsAdapterTests whose setUp always requires
PostgreSQL. It tests route-shaped requests against Operations application services,
including authorization ordering, command translation, read projection parity,
registration metadata, preparation/replay and a durable deployment chain with fake
effects. HTTP and MCP are surface values passed directly to handle(); there is no
HTTP server, MCP transport client or credential-verification exchange in this file.

The database class requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit
psycopg connection, installs the schema and truncates cpk_workspaces CASCADE before
each test. tearDown closes that connection without a second truncate. Even tests
in this class that only use recording services inherit this setup. Its unit_of_work
opens fresh connections through the actual
[Postgres unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
No imports, schema setup, SQL or tests were executed for this companion.

operator_principal defaults to an OPERATOR granted every PolicyScope in workspace-a
and missing; worker_principal defaults to a WORKER with EXECUTION_OPERATE in
workspace-a. These helpers directly construct authenticated-principal values with
a test issuer. Negative cases narrow or move grants. Payload actor_id, actor_scopes
and worker_id fields are adversarial inputs in selected cases, not authoritative
credentials. The selected
[identity contract](../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py)
derives command context from the principal's matching workspace grant and requires
its scopes to agree; actor_id comes from the principal subject.

RouteRequest is a frozen dataclass containing ordinary mutable dicts and a principal.
RecordingService records commands and returns only their class name through a
DescriptorResult. RecordingDeploymentProgram records preparation commands and returns
a supplied projection. GeneratedIds increments a prefix counter; the separate ids
helper consumes an explicit sequence and raises if exhausted. These are in-process
doubles, not durable side-effect or transaction witnesses.

The corresponding [cpk_server.py owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py)
is an adapter over existing service commands and read projections. Its ordinary
argument merger copies payload then path, so path values win. Several tests labelled
MCP still supply path_parameters; those command tests exercise this merger, not a
strict wire-level MCP shape. Closed read routes separately require exact dicts and
surface-specific key placement, reject duplicate path/payload identities and accept
only declared pagination fields. Their envelope check can precede authorization;
workspace denial is tested before cursor/run decoding and store access for selected
well-shaped envelopes, not before every possible payload operation.

The first contract test exercises run start, deployment execute and graph advance
on both surfaces with slash/run-canary. Each must raise InvalidOperationCommand
without cause/context or the canary in repr and leave all command recorders empty.
The same malformed run in read.run-events must produce a fixed 400 page error on
both surfaces before a forbidden unit-of-work factory is called. Selected
[lifecycle](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py),
[coordinator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
and [advancement](../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
command constructors perform canonical run-ID validation. This is one malformed
identity, not the complete RunId grammar matrix.

A second contract test puts the malformed run behind a foreign-workspace worker
principal for those three commands on both surfaces and requires 403 with no
callbacks. Despite its name mentioning cursor decoding, that method has no read
request. A denied read.run-events request with a malformed cursor/run is instead
included later in the trusted-worker test; it asserts 403 and no unit-of-work entry.

Claim-generation rejection covers three post-claim commands, two surfaces and six
candidates: absent, bool, zero, negative, 2**63 and a text canary, for 36 cases.
Each must return the fixed 400 claim_generation error and leave the selected recorder
empty. The owner requires exact int in 1..2**63-1; this test does not exercise the
maximum accepted value. A separate HTTP-only case for each command supplies a
hostile worker_id with generation 7 and requires both authority and
[lease fence](../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
to use trusted-worker from the principal.

Claim deliberately treats the route's run_id coordinate as an execution request ID:
request/legacy with lease_duration_seconds=600 reaches ClaimAndOpenActivityRun and
preserves that request ID. Supplying only the obsolete lease_expires_at form instead
returns 400. This does not test rejection when both old and new fields coexist or
every lease-duration boundary. Unsupported recovery on both surface labels returns
501 for a noncanonical run while an empty dict subclass raises on selected access
hooks. The source still merges arguments and checks trusted context before 501;
the empty subclass fixture is not proof that every mapping implementation is untouched.

The policy-catalog test compares exactly the policy-map keys with IDs from the Core
[HTTP route factories](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py).
It protects route coverage, not correctness of every policy's scopes/kinds or a full
HTTP/MCP authorization matrix. Other tests prove selected boundaries: forged payload
scopes cannot authorize an ungranted principal; a foreign-workspace read cannot
reach its store; and a foreign-workspace approval request produces a fixed, cause-
and context-free denial omitting both workspace IDs before command service access.

Approval request and decision provenance tests require actor/scopes from the trusted
principal even when payload fields name another actor, stronger scopes or an integer
among scope strings. A RUNTIME_AUTHORITY_USE-only principal cannot admit deployment,
and an INSTANCE_WORKSPACE_READ-only principal cannot set desired graph. Preparation
also rejects foreign workspace and PLAN_REQUEST-only principals before calling its
program; source policy requires both workspace edit and plan request. These focused
negative cases do not cover every principal kind, absent principal, or every subset
of scopes for each route.

Seed helpers intentionally establish different amounts of durable truth. seed_workspace
creates a workspace and empty current graph/pointer. seed_session_action additionally
writes an open session and a raw action payload containing an api_token canary.
seed_reviewable_plan writes desired graph/pointer, a session and an empty plan pinned
to current/desired projections. seed_run_event then inserts approval, decision,
execution-request, run and event rows with SQL. Those read fixtures do not obtain
all their history by running the public command chain.

Read tests require current-graph output to contain actual graph ID/name instead of
a demo service/payload echo, and MCP workspace output to retain current graph truth.
Session-action and run-event pages must match across surfaces with the exact five
page-envelope keys; the action API token must be <redacted>, and event note must
survive. Six temporal collections compare HTTP/MCP envelopes: activity, sessions,
session plans, session approvals, pending approvals and plan runs. Selected parent
collections must contain seeded identities. These small first-page fixtures do not
exercise multi-page traversal, query counts, index selection or concurrent changes.

The observation test stores two distinct verification subjects for one node, one
liveness check and one root-response check with expected body digest/match metadata.
Both surfaces must return equal pages with no next cursor and both subjects/check
IDs in order. It does not perform HTTP verification or compare every nested evidence
field. Operator overview is tested for foreign-workspace denial before store access
and equal HTTP/MCP workspace/kind output after seeding. Its excluded disclosure
canary is not introduced by the seed_run_event helper, so that particular absence
assertion is weaker than the action-redaction test with an injected canary.

Closed read-argument negatives cover five journal cases and nine temporal/scalar
cases, including duplicate workspace placement, offset/direction/unknown/cursor
fields and pagination fields on scalar details. Each requires 400 before a forbidden
unit-of-work factory. A hostile payload dict subclass with a raising __iter__ must
also be rejected before store entry. These cases protect selected envelope forms;
they do not establish a universal safe decoder for all custom mappings or command
routes, which use different argument handling.

Three cursor mismatch cases change workspace, session or collection on a session-
actions request and require 400. A malformed cursor containing a secret canary yields
a bounded cause/context-free error. Foreign-workspace journal requests on HTTP and
MCP must instead yield fixed 403 before cursor decoding/store entry, omitting canary
and workspace identifiers. Selected [paging contracts](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
check exact cursor shape, collection/scope agreement and collection-specific limits.
These mismatch tests use actual service factories without a forbidden-UoW sentinel;
the source, rather than those assertions alone, locates their decoding before SQL.
A separate missing-workspace read must return 404 without the supplied secret canary
or SELECT text; it does not inject an actual SQL exception.

The setup-route test uses real workspace/product/image-pull/desired-graph/session
services. It creates a workspace, registers a reference-only image-pull authority,
imports an inline product document, starts a session and selects desired graph.
Queries verify current/desired pointers and session workspace. Products use synthetic
OCI digests and pure Docker topology compilation; there is no registry fetch, image
pull or container start. Separate tests require a configured image-pull service
(501 otherwise) and public idempotency_key for image authority and product import
(400 when absent). In the inspected adapter those keys are checked as text but are
not passed to those registration commands; the missing-key tests do not prove a
keyed durable replay protocol for them.

Ingress registration/read/revoke tests reject PLAN_EXECUTE-only callers and exercise
successful metadata operations with the default broad operator grant. They compare
registration/list/detail identities, use the store to confirm an allowed hostname,
and assert revoked status. The registered descriptor excludes selected token/bearer
canaries. No DNS/tunnel call or secret resolution occurs. Payload actor_scopes in the
successful cases do not themselves establish least-privilege positive admission.

Runtime authority tests reject execution-only registration/read, distinguish delivery
registration from authority registration, and distinguish revoke from register.
Successful registration/list/detail paths redact the synthetic remote Docker endpoint;
delivery list redacts secret_references, and revoke returns revoked status. This
covers real authority/delivery metadata persistence with selected descriptor checks,
not TLS connectivity, file delivery, Docker API calls or every field's redaction.

Secret-provider tests deny six unrelated grants for registration and prove trusted
admitted_by despite forged payload actor/scopes. Register alone cannot read, read
alone cannot revoke, and focused grants permit the corresponding operations. Provider
and reference list/detail results deliberately expose public reference strings,
including credential_reference; they do not expose credential bytes. A caller granted
workspace-b still gets 404 for workspace-a's reference ID without that ID in the
message. Reference/provider revocation records trusted-revoker. Aggregate output
checks omit selected raw-address/token/plaintext/ciphertext/bearer canaries; most are
not injected into successful records.

Four additional secret-provider payload cases actually inject a raw endpoint URL,
a raw credential value, secret-shaped metadata key or bearer-bearing metadata value.
Each must return 400 without those raw values in the error descriptor. The source
maps some registration errors using str(error) with chaining, so these assertions
must not be generalized to universal cause-free or value-redacted errors.
CpkServerApplicationError itself validates status and nonempty message, without a
global maximum message length or general secret scrubber.

The delegation-key test registers a provider and two reference-backed public-key
metadata values, activates key-a then key-b and requires key-a VERIFY_ONLY/key-b
ACTIVE in equal HTTP/MCP listings. List output omits PEM and private-reference
material. Verifier configuration includes both key IDs and the audience derived
from a supplied gateway node ID, without private_key_reference. Register-only cannot
read. This tests overlap metadata/projection behavior; despite retire scope in the
fixture principal, it does not call retirement, generate key bytes, sign a token,
prove cryptographic validity or apply the configuration to a real gateway.

Preparation translation uses a recording program returning each of three closed
results: no-changes, review-blocked and approval-required. HTTP/MCP results must
exactly equal the small expected coordinate/status mappings, and translated commands
must be equal with selected trusted actor, graph, lineage, revision and key fields.
Title/comment disclosure canaries and desired_graph must not appear in output. The
actual owner uses exact result types, but this test does not supply an unsupported
result subtype, malformed preparation mode or saved revision input.

The durable no-change preparation test composes actual operation-session, desired-
graph, planning and approval services through
[DeploymentProgram](../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py).
It prepares the current graph through HTTP then MCP on the same service instance,
requires equal no-changes responses, one session and one plan, and zero approval
requests, execution requests, runs and effect attempts. It does not count every
history/graph table or restart a process. Separate recording tests protect selected
RequestActivityPlan and RequestApproval translation fields. A real approval loop
requests a plan review, reads pending queue/detail and records an approved decision
from a manager principal, without executing the plan.

The long public deployment-chain test constructs cpk_server_services and
CpkServerOperationsApplication with real workspace/product/session/planning/approval/
admission/lifecycle/coordinator/start/fold/reconciliation/advancement services. A
SucceedingActivityAdapter records activity IDs and returns successful activity or
runtime-effect values; _ForbiddenObserver raises if reconciliation tries to observe.
No provider resource is created. cpk_server_services supplies DeploymentProgram when
operations and desired-graph services are present. Calls derive coordinates from
prior responses rather than hardcoding every generated plan/run ID.

The chain creates workspace, imports a product, prepares/replays the deployment,
reads plan/approval/session pages, approves/replays, admits/replays, claims/replays
and starts/replays. Decision, admission, claim and start replay checks require only
the replayed flag to change. It then permits at most 15 execution calls with
max_effects=1, alternating surfaces. For progressed/completed calls, opposite-surface
replay must preserve the exact response, recorded adapter activity sequence and
visible run-event page. A nonprogress status branch checks that current graph has
not moved and exits, but the final assertion still requires completed and succeeded;
that branch is not acceptance of an uncertain or unsupported chain.

After completion, graph advancement must preserve the expected old/new authored and
projection coordinates and desired revision. The opposite-surface advancement replay
must report replayed and the same target projection. Both read surfaces must show
the desired graph/projection as current and one succeeded run. Returned event IDs
and adapter activity IDs must be unique. These assertions protect same-process
idempotent replay for this fixture with fake effects and first pages of up to 100
items. They do not prove restart safety, all event ordering/content, arbitrary long
histories, ambiguous commits, lease takeover, real cleanup or provider compensation.

A standalone unsupported-service test requires 501. The final test named
application_boundary_requires_one_service_for_every_role supplies every role with
an unsupported service and only verifies a routed read returns 501. It does not
remove a role or assert constructor rejection for missing services, although the
source constructor has that check. Desired-draft routes, saved-input preparation,
gateway-probe execution, broader principal-kind matrices and several malformed
source/result paths also have no direct cases in this file.

Reading depth is the complete test and complete 2,169-line cpk_server.py owner,
selected identity, paging, claim/start/execute/advance command validation and route-
factory contracts, full lease-fence value, plus previously read full Postgres unit
of work and deployment-program owner. This is not a complete audit of every imported
service, SQL store or Core language. No source, test, inventory or publication was
changed; the cpk_server.py source companion remains a separate review slice.
