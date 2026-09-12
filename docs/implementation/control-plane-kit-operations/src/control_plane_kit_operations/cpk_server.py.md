Source: [control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 2,169-line owner adapts route-shaped HTTP/MCP requests to Operations command
services and read projections. Its objects are request/service protocols, a small
application error, authorization policies, a service map and role-specific adapters.
The main transformation is principal plus route arguments into a trusted command
context and an existing typed command, followed by a result descriptor. It does not
host HTTP/MCP, verify credentials, open a Docker client or define the durable laws
of the imported command services. Injected execution and probe services can perform
effects; wrapping them here does not make the whole application effect-free.

CpkServerRouteRequest declares surface, route_id, service_role, path_parameters,
payload and AuthenticatedPrincipal. CpkServerApplicationService declares handle()
returning a mapping. These are structural protocols, not runtime validators of all
request/dependency attributes. The owner has no explicit __all__; the Operations
[root exports](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py)
include its request/service protocols, application/error, role adapters and
cpk_server_services. RouteAuthorizationPolicy remains an owner-defined helper rather
than one of those root exports.

CpkServerOperationsApplication is a frozen dataclass retaining the supplied services
mapping. Construction checks that every ControlPlaneServiceRole is present, but
does not reject extra entries, copy/freeze the mapping or validate each handler.
handle selects by the request's service_role and calls that service. A missing key
maps to a chained 404 error. This outer dispatcher does not independently compare
route ID with the route catalog's expected role; role adapters interpret/reject the
route they receive. Freezing the container is not deep dependency immutability.

cpk_server_services composes planning, approval, admission, lifecycle, execution and
read adapters around supplied services. Recovery and authorization roles receive
explicit unsupported placeholders. Observation receives a GatewayProbe adapter when
a probe service is supplied and an unsupported placeholder otherwise. If no
DeploymentProgram is supplied but operation-session and desired-graph services are
available, the factory constructs one from those services, planning and approval,
with a SavedDeploymentPreparationService sharing the supplied unit-of-work factory.
It does not execute preparation at composition time or construct provider clients.

CpkServerApplicationError stores status and message and exposes them under an error
mapping. It validates exact integer 400..599 status and nonempty string message.
It has no universal message-length bound, control-character filter or secret-value
scrubber. Fixed errors in selected paths are narrower guarantees than the class's
bounded-error docstring. Many adapters return result.descriptor() structurally;
there is no global output schema or recursive redaction pass over every return.

_trusted_context extracts workspace ID, requires an AuthenticatedPrincipal, derives
its command context and applies the route's policy. The selected
[Core identity contract](../../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py)
requires a matching workspace grant and congruent granted scopes; actor_id is the
principal subject. Payload actor_id, worker_id and actor_scopes do not determine
command authority. Credential verification and the trustworthiness of the supplied
principal belong to the process wrapper, not this owner. Missing principal is 403;
a workspace-grant failure is translated after leaving its except block into a fixed
cause/context-free workspace denial. Unknown route policy becomes a chained 404.

RouteAuthorizationPolicy checks principal kind, every required scope and at least
one any_scopes alternative when present. Operator is the default kind. The shared
worker-operation policy accepts WORKER or SERVICE with EXECUTION_OPERATE for claim,
start, execute, advance and recovery. Standard workspace reads require workspace-read;
workspace edits and session commands require workspace-edit; workspace creation uses
HUB_INSTANCE_CREATE, still through a matching workspace context. Planning/preparation,
approval/admission, runtime/ingress/secret/delegation metadata and probe routes each
have explicit focused policies.

Preparation requires workspace-edit and plan-request. Approval decision accepts
plan-approve or destructive-plan-approve at the route boundary; the imported approval
service owns decision-specific policy. Gateway probe requires gateway-probe-use,
delegation-key-use and secret-provider-use. Read, use, register, delivery-register,
activate, retire and revoke scopes are not inferred from one another. The policy
map is data local to this adapter; checking that a route has an entry does not prove
every policy or external authentication configuration is correct. This module does
not centrally reload every session/plan/reference named by a payload to prove its
workspace membership; any additional membership checks must be traced through the
receiving service and store.

Ordinary _arguments copies payload then path_parameters, with path values winning.
Most command routes and nonclosed reads use this permissive merger even for an MCP
surface label. It does not reject duplicate identity placement, unknown keys or
mapping subclasses. _trusted_context may therefore read/copy mappings before a
workspace denial; authorization-first means before selected semantic decoding and
service access, not before all input operations. Text helpers require nonblank
isinstance(str) values and return the original text without general size/control
bounds. Mapping helpers use isinstance(Mapping), and string mappings check item
shapes without a general count limit.

CpkServerReadService has a stricter declared collection/detail surface. Closed reads
require exact dict path/payload values. HTTP must carry exactly required identities
in path and only allowed optional arguments in payload; MCP must have an empty path
and carry all arguments in payload. Paged forms allow limit and after; scalar forms
do not. Duplicate identities and unknown fields are rejected. Most closed reads
check this envelope before _trusted_context. Draft-prefixed reads additionally call
_trusted_context before the envelope check, then call it again with prepared values.

After authorization, declared paged collections become ReadPageRequest values with
workspace/session/plan/run/draft/revision scopes. The selected
[paging contracts](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
validate canonical run IDs, exact cursor shapes, collection/scope congruence and
collection-specific maximum page sizes. The adapter's positive-int helper alone
checks only a lower bound; the page contract supplies the upper bound. Page errors
become a fixed 400 after leaving the decoding except block. Draft revision detail
also validates its draft scope and bounded revision before opening a unit of work.

For a read, the adapter opens one unit of work and constructs
[InstanceReadService](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/instance.py)
with workspace, graph, history, execution, observation, authority, secret, gateway
and delegation stores. Draft-related routes add draft/revision stores; overview
adds draft and saved-preparation stores. The optional clock is passed through.
Store attributes are accessed during construction even when a particular route does
not use every projection, so malformed/incomplete store bundles can fail before
_read_model's error handler.

InstanceReadService composes separate workspace/graph, history, observation,
authority/secret, gateway/security, draft/revision and overview projection owners.
The adapter selects their methods; it does not rebuild durable truth from response
payloads. _read_model maps graph pointers/control surface, overview, activity/session/
plan/approval history, observations, authority/reference metadata, probe history and
delegation/verifier configuration. Paged routes require the prepared collection;
details construct their own typed identities. Overview uses its own limit/cursor
path inside _read_model, rather than _PAGED_READ_COLLECTIONS preparation. The
read facade is therefore not a promise that all cursor decoding occurs before SQL.

ReadModelError and ReadPageError from _read_model become application errors. Page
errors are 400. Read-model status selection uses message prefixes/substrings: selected
missing-object messages become 404, unconfigured stores 503, selected graph-truth
conflicts 409, and others 400. The adapter carries str(error) into that response,
without a universal secret scrub. Conversion occurs outside the catch, clearing
that immediate exception context. Store construction, unrelated SQL/errors and final
descriptor construction are not all covered by this handler.

The read path requests commit and evaluates model.descriptor() before leaving its
unit of work. The actual
[Postgres unit of work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
physically commits on successful exit after that evaluation; a descriptor exception
can still roll it back. One unit of work is not, by itself, proof of a particular
isolation level or cross-query snapshot law. This adapter makes no direct read-path
business mutation, and no general journal event is added merely for serving a read.

Redaction belongs to each returned model/record. The selected
[authority/secret projection](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/authority_secrets.py)
recursively redacts runtime/ingress/delivery descriptors but intentionally returns
public secret-provider/reference descriptors after type checks. Those descriptors
may expose reference strings, including credential references. The imported
[recursive helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/_redaction.py)
filters sensitive key names and selected address/environment fields; it does not
scan every scalar string for secret bytes. Command registration descriptors likewise
need their own owner-level validation/redaction. References and raw secret material
must not be conflated when reviewing these surfaces.

CpkServerPlanningService first derives trusted context, then translates a broad
setup/preparation family through optional injected services. Missing required service
configuration produces 501. Workspace creation constructs CreateWorkspace with
trusted actor, key and text metadata. Product import decodes an inline document and
optional descriptor source, defaults source to InlineDescriptorSource, then calls
ImportProductDescriptorCommand. Image-pull authority registration constructs its
reference-bearing authority and calls RegisterImagePullAuthorityCommand. Product
responses curate registration/reference/status/display metadata; image-pull responses
include the authority descriptor and metadata.

The product/image registration routes require an idempotency_key text field but do
not pass that key into their imported registration commands. Runtime/ingress/secret/
delegation registration helpers also check a key without inventing an adapter-owned
keyed history record. Their durable duplicate/replacement semantics belong to the
services/stores. The presence of a required public key field alone is not evidence
of one shared replay protocol across every command route.

Runtime authority registration accepts local Docker socket or remote Docker TLS
values, with typed secret references for certificates/key. Delivery registration
uses RuntimeAuthorityAccessDeliveryCodec. Register/revoke commands receive trusted
actor/scopes where their contracts require them. Ingress registration accepts the
Cloudflare zone authority shape, hostname pattern, API reference and generated-secret
provider/prefix; it calls metadata registration, not tunnel allocation. Registration
families map selected authorization errors to 403 and malformed/domain errors to
400, commonly using chained str(error). The adapter itself does not call Docker,
Cloudflare, DNS or a secret provider to validate that metadata remotely.

Secret-provider/reference commands decode closed provider kinds/intents, bounded
nonempty text sequences and typed endpoint/secret references, then call the respective
register/revoke methods with trusted actor/scopes. Provider registration can carry
supersedes_registration_id and metadata. Secret errors map to 403, fixed missing-
metadata 404, conflict 409 or malformed 400; several retain the originating exception
as cause. Delegation-key commands similarly translate register/activate/retire/revoke,
with default gateway-probe purpose and Ed25519 algorithm. Public key material and
private-key references are command inputs; the service owns key-state transitions.
The adapter neither generates nor signs with a private key.

Desired-graph set decodes a graph and constructs SetDesiredGraph with session,
trusted actor, key, optional expected authored/projection IDs and desired revision
(defaulting to zero when absent). Deployment plan constructs RequestActivityPlan
with required authored coordinates and optional projection/revision expectations.
Neither handler compiles the deployment diff itself. They return imported service
descriptors, leaving graph admission, locking, revision checks and action persistence
to those owners.

Desired-topology draft routes construct the four
[draft command values](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/desired_topology_drafts.py).
Create/revise decode a graph; create passes the title through to the service, while
revise requires draft ID and expected head. Select requires both expected desired
lineage keys to be present, allows their values to be None and requires a revision
plus expected desired generation. Delete requires expected head. The revision helper
accepts exact positive bounded int or at most 19 ASCII decimal characters converted
to that integer; the upper bound is 2**63-1. These commands' complete admission laws
are not duplicated here. Execution errors map draft conflict to fixed 409 and other
draft errors to fixed 400 with suppressed chaining; some argument construction occurs
before those service-execution catches.

Deployment preparation accepts exactly one input mode: inline desired_graph, or a
saved draft_id/revision pair. It constructs PrepareDeploymentProgram with trusted
context, expected current lineage, optional expected desired lineage, desired
revision, title/key and optional approval comment. Lineage mappings require exactly
authored_graph_id and realized_projection_id. The imported
[DeploymentProgram](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py)
owns preparation's sequential service calls and replay semantics; this route does
not wrap them in a new transaction or execute the resulting plan.

Selected preparation authorization/state/malformed errors map to fixed 403/409/400
with from None. That suppresses displayed chaining but does not guarantee the
exception's __context__ is absent. Helper-raised CpkServerApplicationError values are
not in that catch list and can keep their more specific argument message. After the
try block, exact DeploymentNoChanges, DeploymentReviewBlocked or
DeploymentApprovalRequired types become small status/workspace/plan mappings, adding
approval_request_id only for the third. Other result types produce 500. This closed
response omits graph/title/comment material; it is not a guarantee about every other
adapter's descriptor.

CpkServerApprovalService translates RequestApproval or DecideApproval, using trusted
actor/scopes and session/plan/request coordinates. Admission translates
RequestPlanExecution with approval request, key and optional readiness attestations.
Readiness must be a list of mappings with activity_id/evidence_ref, but this helper
does not independently cap its length or verify those external facts. Imported
approval/admission services own approval decisions, execution eligibility, policy,
transactions and durable history; a route scope check does not replace those laws.

CpkServerLifecycleService dispatches operation-session start/close/cancel/record-action,
current-graph advancement and claim. Session commands use existing workflow values;
record-action validates its enum before passing a payload mapping. Claim interprets
the public run_id path coordinate as request_id, with request_id payload fallback.
It requires an exact integer lease_duration_seconds in 1..3600. This preserves the
request/run identity distinction; it does not parse that request coordinate as RunId.

Start and execute live in CpkServerExecutionService; advancement remains in the
lifecycle adapter. All three post-claim commands build authority and
[ExecutionLeaseFence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
from the trusted subject and exact integer claim_generation in 1..2**63-1. The
imported command constructors validate canonical run identity and authority/fence
agreement. The adapter does not itself load the current durable lease or decide
whether it is stale. Execute accepts positive exact-int max_effects defaulting to
one, with no separate adapter upper bound. Advancement forwards pinned authored/
realized coordinates and desired revision to its command service.

CpkServerGatewayProbeService first rejects an unrelated route, then authorizes and
constructs RequestGatewayProbe with typed kind/target, expected current graph,
gateway node, optional path and access path defaulting to runtime-private. The
selected [gateway command contract](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_probes.py)
retains trusted context and request identity; its service owns admission, persistence,
dispatch and result recording. The adapter maps selected errors to 403/404/409/400
with chaining. A runtime-private default is an access-path value, not proof that
an arbitrary supplied topology or dispatcher is network-safe.

CpkServerUnsupportedService still calls _trusted_context before returning 501. It
does not implement recovery, adopt uncertain effects or authorize bypassing a failed
activity. More generally this owner appends no independent session/action/attempt
history around all commands and adds no global transaction, compensation or retry
loop. Those guarantees must be traced through the selected command service and
store; provider uncertainty is not resolved by a successful descriptor conversion.

The complete 3,704-line
[test_cpk_server_adapters.py](../../../../../control-plane-kit-operations/tests/test_cpk_server_adapters.py)
contains five pure contract tests and 38 tests inheriting PostgreSQL setup. It
protects selected canonical-run/claim-generation inputs, trusted provenance,
authorization-before-service ordering, policy-map coverage, HTTP/MCP result parity,
closed read arguments, metadata scope/redaction, preparation results and durable
replay. The tests call handle directly; several MCP-labelled command cases retain
path arguments and are not transport-level conformance tests.

Its long deployment chain uses real coordinator/start/fold/history/advancement owners
with a success-effect stub and a forbidden reconciliation observer. It must finish
completed/succeeded, preserve response/adapter-call/visible-event evidence on replay
and advance current graph only after completion. This is same-process evidence for
one small graph, not a provider, restart, lease-takeover or cleanup proof. The final
application-map test supplies every role and asserts an unsupported read returns
501; it does not test rejection of an incomplete map. The file does not directly
exercise saved/draft preparation routes, gateway-probe execution, every principal
kind/scope combination, every error mapping or all service/result failures.

Reading depth is the complete owner and that complete test, full read-services export
facade and InstanceReadService composition, full recursive read redactor and lease-
fence value, plus previously read full Postgres unit of work and DeploymentProgram.
Selected imported reads cover identity, paging, command run validation, authority/
secret projection paths, draft values, gateway command/entry admission and root
exports. They do not constitute a full audit of every delegated service, database
store, projection or Core language. This documentation change ran no imports/tests,
DB/provider/credential calls or live effects; the test companion is a separate slice.
