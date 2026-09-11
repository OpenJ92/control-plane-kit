Source: [control-plane-kit-operations/tests/test_runtime_interpreter_dispatcher.py](../../../../control-plane-kit-operations/tests/test_runtime_interpreter_dispatcher.py).
Maintain this document alongside its source file. Recheck dispatch-arm ownership,
context/request admission, graph selection, raw result preservation and secret
grant correlation when these tests or their owners change.

The 1,263-line file contains 27 database-free tests and their local interpreters,
authorizer, contexts, graph/product builders and endpoint-size helpers. It uses
real Core and Operations values with recording test doubles at the effect and
secret-authorization boundaries. It does not invoke ExecutionCoordinator,
durable attempt admission, PostgreSQL, Docker or a secret provider. No tests,
application imports, database connections or provider actions were executed
while authoring this companion.

## Values supplied to the dispatcher

context_for builds one PlannedActivity and ActivityPlan, a claimed execution
request, a running ActivityRunRecord, an ActivityPlanRecord, base and desired
identity projections, one registered product, worker authority and generation-1
fence, optional runtime-authority registrations and a STEP_STARTED intent event.
These are constructed records with fixed timestamps, not facts loaded from a
store or an intent event committed by the coordinator. The helper's default
worker scope is EXECUTION_OPERATE; selected secret tests add SECRET_PROVIDER_USE.

projection_record_from_graph first encodes an authored GraphVersionRecord, then
constructs its identity projection. The base has version 1 and desired version
2. graph_with_node creates api under runtime-a with product identity and digest
metadata. Its runtime kind and optional authority reference are parameters;
graph_without_node creates only the runtime. These fixtures let translation
choose between deliberately different base/desired runtime kinds without any
provider discovery.

_registered_product constructs an inline hello-server descriptor with a synthetic
OCI digest, HTTP provider socket and port 8000. Its optional secret delivery is
the application control token in APPLICATION_CONTROL_TOKEN, represented by a
SecretReference and SecretUseIntent. This is declaration material; no image is
pulled and no environment variable receives actual secret bytes.
_registered_runtime_authority constructs a registered local Docker socket
authority value. Registration construction does not connect to Docker or prove
that the authority is operational.

RecordingInterpreter records requests and returns a supplied result or synthetic
success. AuthorityAwareRecordingInterpreter also records the supplied authority
and returns its reference in synthetic evidence. RecordingActivityAdapter has
separate legacy and runtime arms; it records contexts and runtime requests, then
returns configured outcomes or runtime success. The local RaisingInterpreter in
the fault matrix raises a provider-canary RuntimeError. These doubles prove
dispatch behavior, not runtime resource creation or successful networking.

RecordingSecretUseAuthorizer records each command and either raises an explicit
SecretProviderAuthorizationDenied or returns a synthetic SecretResolutionGrant.
The grant uses fixed authorization/registration identities and fingerprint,
provider endpoint and bootstrap credential references, and command-derived use
coordinates. The double does not evaluate scopes, consult registrations, commit
an authorization or resolve a credential. Its grant constructor checks a value
contract, not the existence of a committed authorization row.

The test class's execute_runtime helper asserts that the runtime arm exists,
builds a request through runtime_effect_request_for_context unless one was
supplied, then invokes that arm. Translation failures can therefore happen before
the dispatcher receives a request.

## Separate legacy and runtime arms

The post-start-request test supplies a request explicitly and requires the
interpreter to receive that same object and the dispatcher to return the same
configured RuntimeEffectResult. Calling the legacy arm with the runtime context
must raise InvalidOperationCommand. No secret uses are present here, so grant
attachment does not replace the request.

The ActivityExecutionDispatcher tests keep ingress on execute and runtime work
on execute_runtime. An ingress outcome is returned by identity, runtime results
retain their exact public type and effect identity, and the context/request lists
show which adapter was called. A separate handled-ingress-failure test preserves
the supplied unsupported outcome by identity. Missing ingress produces
ingress.interpreter-missing with activity/operation details and no runtime call.
The dedicated StartRuntime case also checks runtime request identity and no
ingress context. These assertions exercise composition through recording adapters,
not real ingress allocation or runtime start.

The socket case passes SwitchSocketConnection through the runtime dispatcher's
legacy arm. It requires success, no failure and the exact evidence descriptor
containing socket-connection-recorded, SwitchSocketConnection and edge-a, with no
Docker request. The actual owner returns this bounded evidence value; the test
does not persist a socket change or mutate a deployment graph.

## Admission before virtual access and interpreter calls

Hostile subclasses of RuntimeEffectRequest and ActivityRealizationContext are
constructed without their ordinary constructors and instrument selected attribute
accesses. The dispatcher must reject them with the fixed exact-context/request
error, no exception chain, no recorded virtual access and no interpreter request.
This protects the outer nominal admission boundary before the selected hooks;
it is not an exhaustive test of every forged nested value or Python hook.

The congruence case swaps requests between otherwise valid run-a and run-b
contexts. It requires the fixed incongruent-context/request error, no exception
chain and no authorizer or interpreter calls. Actual
[dispatcher admission](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
also compares workspace, request, plan, base/desired graph, intent-event,
activity and operation coordinates. This method independently varies the run
coordinate; it does not parameterize every comparison or rederive all request
material from the context.

The malformed-retained-run case fails even earlier. Constructing context_for
with retained/run-canary raises OperationsRecordError with the exact malformed
run-identity message, no cause/context, bounded combined string/repr and no
canary. Both double call lists remain empty because record construction failed;
this is not a malformed record successfully reaching dispatch.

## Selecting graph material, interpreter and authority

StartNode uses the desired graph's Docker runtime when the base is DRY_RUN;
StopNode uses the base Docker runtime when the desired graph is DRY_RUN. The
tests check the selected interpreter's evidence/request runtime kind and absence
of requests to the other interpreter. ReconcileRuntime selects the desired
DRY_RUN record and returns that interpreter's evidence. These are representative
operations, not a matrix over every runtime/node operation.

The actual [request translator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
selects base material for stop/remove node and runtime operations and removal of
a socket connection; other operations use desired material. RemoveNodeResource
with a missing base node raises the fixed runtime-effect-node-target-is-missing
InvalidOperationCommand, without an exception chain or Docker request, despite
the desired graph containing api. Although the test name says unsupported, its
asserted result is a translation exception, not an UNSUPPORTED runtime result.

An AWS request with only Docker configured yields an exact RuntimeEffectResult
of kind UNSUPPORTED and runtime.interpreter-missing, including activity,
operation and aws details; Docker receives no request. The name's without-attempt
wording means no interpreter invocation in this file. No durable attempt service
is present, so the test cannot establish whether a surrounding coordinator has
already persisted STEP_STARTED or an effect-attempt record.

The registered-authority case forwards the matching authority to an
authority-aware interpreter, checks the authority-reference evidence and request,
and compares the recorded authorities with the expected list. A missing
registration yields runtime.authority-missing before either recorded interpreter
arm is called. A registration with an interpreter lacking execute_with_authority
yields runtime.authority-interpreter-unsupported with no request.

Actual selection searches the context's supplied registrations by reference and
runtime kind. It does not query current provider state or independently refresh
registration status. These tests use a local Docker authority; they do not prove
remote TLS credential resolution, transport authentication or authority revocation.

## Direct uncertainty and preservation of valid raw results

The fault matrix supplies a legacy ActivityExecutionOutcome on the runtime arm,
a hostile RuntimeEffectResult subclass, an interpreter exception and a result
with another effect ID. Each must become an exact RuntimeEffectResult for the
requested effect, with kind UNCERTAIN, runtime.provider-result-unknown and bounded
boundary/reason details: interpreter plus invalid-result-type, exception or
effect-id-mismatch. Selected hostile hooks remain untouched, and the provider
canary must not appear in result/failure representations. A separate mismatch
test checks the same identity and uncertainty classification.

This is an uncertainty result returned directly by the dispatcher. It is not a
durable fold, reconciliation decision, retry or permission to redispatch. The
actual owner catches ordinary Exception around interpreter invocation; these
tests do not exercise BaseException interruption or every authorizer failure.

A configured failed runtime result preserves the FAILED kind and supplied
failure code/details. A normal endpoint case preserves an exact
RuntimeEndpointObservation with the expected subject, graph and private-runtime
context. The maximum-shape case additionally requires the endpoint object itself
to survive by identity in the raw result tuple. The dispatcher does not turn
these observations into persisted Operations records in this suite.

The endpoint helpers construct the descriptor of a valid literal HTTP endpoint,
replace its subject in a temporary dictionary and measure compact sorted JSON
for the runtime_endpoint envelope. _subject_for_bridge_evidence_size combines
emoji and ASCII padding to reach 4096 encoded bytes while remaining within 512
subject characters. json.dumps uses its default ASCII escaping, so this measures
encoded JSON rather than the subject's unescaped UTF-8 size. The actual
[Core endpoint constructor](../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py)
checks those character and envelope-byte bounds using the same ASCII-escaped
envelope shape. This fixture proves one accepted boundary value remains intact;
it does not test over-limit rejection, every field at its maximum, or all
Operations evidence envelopes.

The ordinary [Core runtime result constructor](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
admits executable result kinds, bounded evidence and exact supported observation
types, requires failure for non-success, forbids failure on success and rejects
failed verification inside success. These constructor checks are dependency
behavior, not independently exhaustive dispatcher tests. Exact outer-result
admission at dispatch does not reconstruct every nested result field.

## Secret grants and stable correlation

The authorized application-token case checks one authorizer command's reference,
intent, worker and effect, then checks the complete expected grant tuple on the
interpreter request, including provider/credential references, registration
identities, fingerprint and request/run/activity/effect coordinates. Session is
None. The actual dispatcher calls authorization before interpreter invocation
and replaces the request with the returned grants. The test inspects recorded
commands and the resulting request; it does not use a shared interleaved call log
to independently measure ordering or assert a real authorization commit.

Missing authorization support produces secret.resolution-authorizer-invalid;
the explicitly denying double produces secret.use-not-authorized. Both prevent
interpreter calls. The denial is controlled by the double's flag, so this is not
evidence that the actual SECRET_PROVIDER_USE scope policy was exercised.

The actual [secret authorization service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
owns scope checks, durable registration/use admission and committing the grant.
The dispatcher checks the returned grant's type, workspace, effect and
permits(reference, intent). In the [Core grant contract](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py),
permits compares reference equality and intent identity; it does not independently
authenticate a provider or prove the grant was committed. This suite does not
exercise malformed grant responses, partial authorization of several uses,
provider revocation, secret bytes or delivery into a real container.

The retry-correlation test calls dispatch twice for the same context and requires
two authorizer commands with equal correlation and absent session. It does not
coalesce calls or establish durable idempotency. The live-grant/reconciliation
named case compares an original context, a later intent timestamp and a new
worker/generation. Timestamp changes leave correlation stable and change
requested_at; changing worker changes correlation. Each command is compared with
secret_use_correlation_for using workspace, reference, intent, actor, operation
request, run, activity and effect coordinates, without session. No reconciliation
service is invoked; the test protects the shared correlation construction that
such a service can use.

## Ownership, security and evidence limits

The mathematical shape is a constructed activity/context translated into a
RuntimeEffectRequest value, selected by runtime kind and interpreted through a
separate runtime arm into a RuntimeEffectResult value. The executable laws here
concern representative dispatch separation, nominal admission, lineage matching,
explicit unsupported/uncertain outcomes, raw-result preservation and grant
correlation. Provider effects remain behind interpreter methods.

The security contribution is early rejection before the selected calls, bounded
fault translation, explicit unsupported authorization paths and reference-only
grant propagation. No new network or mutation surface is introduced by this
documentation. The fake authorizer and authorities do not establish real policy
enforcement, secret custody, Docker access or provider safety.

Durable command receipts, start/fold ordering, database rollback, reconciliation,
lease fencing, graph advancement and lifecycle settlement belong to the
[coordinator companion](../src/control_plane_kit_operations/coordinator.py.md)
and its separate composition/contract suites. Reading this file provides source
evidence for its stated assertions; it is not fresh green test evidence or live
acceptance evidence.
