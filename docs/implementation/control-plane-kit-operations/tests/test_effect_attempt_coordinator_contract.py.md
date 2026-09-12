Source: [control-plane-kit-operations/tests/test_effect_attempt_coordinator_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_coordinator_contract.py).
Maintain this document alongside its source file. Recheck fixture substitutions,
issued-command/result correlation, error admission and the exact import/call
tables when these tests or their owners change. Preserve the distinction between
behavioral laws and structural snapshots when updating expectations.

This 1,844-line file contains 21 test methods, local policy/result-building
helpers and large explicit import/call expectations. It protects the coordinator
as a composition boundary over start, direct fold and reconciliation services,
and checks selected ownership declarations. It does not prove PostgreSQL command
admission, transaction safety, runtime deployment or provider observation. No
test, application import, database connection, architecture-policy evaluation or
provider action was executed while authoring this companion.

## What the harness actually executes

The fully read [coordinator fixture](effect_attempt_coordinator_fixture.py.md)
constructs real typed Core/Operations values but injects recording services. Its
DBFreeExecutionCoordinator overrides execute to call _execute_admitted directly,
and _load_context returns a pinned in-memory context. This bypasses ordinary
command-receipt admission/completion and the public execute scope check; it does
not substitute a database with a faithful durable model. UoW, clock and ID calls
are guarded by ForbiddenInteraction, while legacy event/outcome writers record
and raise. An empty effect_ledger means those supplied guards were not called;
it is not a durable activity log or proof about every possible external effect.

The pinned context uses one StartNode activity, prepared graph/product records,
worker-a/generation 7 and an initially empty journal projected by Core. Service
results do not update this context, journal or schedule. As a result, repeated
loop selections can revisit the same ready activity even after a supplied fold
result says it succeeded. These tests usually assert effects_attempted and calls,
not a truthful final deployment status.

Recording services consume queued values and raise only their exact named service
errors or RuntimeError; the fold service also invokes queued callables. Empty
queues fail through pop. RecordingCoordinatorAdapter records a runtime call,
then raises a queued BaseException or returns its value; its legacy arm records
the context and returns success. This differs from the PostgreSQL fixture's
adapter, which raises only exact TypeError/RuntimeError. RecordingLifecycle is a
separate guard and fails if called without a queued result.

The fixture builds admitted start records, direct/recovery-shaped attempts and
fold results with constructors and evidence fingerprints. fold_result_for creates
a state/event/outcome record from the supplied command; it neither calls the real
fold service nor persists anything. The recovery helper prepares a record with
a recovery decision without exercising an uncertain-history approval workflow.
The constructor compatibility branch in db_free_coordinator is test scaffolding;
the current constructor takes the ordinary dependency path.

Imported context_for and _context helpers prepare different realization contexts
with graph projections and inline registered products. Their sample OCI digest
and authority metadata are values, not fetched images or opened Docker sockets.
The source imports architecture_testing and Operations directly, so missing
dependencies can fail collection. The normal package harness supplies the pinned
architecture-testing sibling and inventory path; this file is not an isolated
standalone installation recipe.

## Accepted controls, constructor and dispatch arms

The first control checks exact types for NewlyStarted, FoldEffectAttempt,
ReconcileEffectAttempt, NewlyFolded and ExistingFold. These are fixture-built
values, not successful service transactions. A second control constructs a Core
request from an intent using the start event ID and empty resolution grants,
then requires intent round-trip equality and agreement with the attempt's request
fingerprint. The local request_for_started helper owns that small construction.

The legacy control routes one AllocatePublicIngress context to the ingress
adapter and one SwitchSocketConnection context to the runtime adapter, requires
ActivityExecutionOutcome results and exact legacy call lists, and requires no
runtime-arm calls. It covers those two representative operations, not every
ingress/socket operation or provider behavior.

Constructor inspection pins the eight parameter names/order and the seven
keyword-only dependencies after unit_of_work_factory. It checks stored identity
of the injected start/fold/reconciliation objects and excludes direct provider/
observer constructor parameters. It does not assert every annotation/default,
validate arbitrary service implementations or exercise their constructors.

Protocol inspection pins ActivityExecutionAdapter's Protocol base, its two method
parameter lists and return annotations, plus runtime-arm method availability and
parameter lists on both dispatchers. Sending a runtime activity to either legacy
dispatcher arm must raise a distinct exact InvalidOperationCommand with the same
fixed args and no cause/context. Actual
[dispatchers](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
separate ingress/socket handling from execute_runtime; the outer runtime arm
delegates to its runtime adapter. Signature checks are not complete behavioral
substitutability tests for every adapter.

## Early stops: compensation and withdrawn authority delivery

The compensation control first proves a realization context can carry a
STEP_COMPENSATION_STARTED event. It then replaces the pinned run status with
COMPENSATING and its events tuple with that event. The precomputed projection and
schedule are not recomputed, so this is a run-status guard scenario rather than
a fully reconstructed compensation journal. It requires BLOCKED, zero attempted
effects and empty service, adapter, legacy-write and effect-guard ledgers.

The actual coordinator classifies COMPENSATING as blocked before effect selection.
The test additionally checks three compensation-related substrings are absent
from inspect.getsource(ExecutionCoordinator.execute). This textual check covers
only that method's source, not all delegated helpers or a semantic absence of
compensation support across Operations. No compensation authority is acquired or
inverse operation executed in this scenario.

The delivery control prepares a graph requesting an admitted local-Docker socket
delivery, then supplies an empty current admission tuple in the pinned context.
It initially allows InvalidOperationCommand or ExecutionCoordinatorDenied but
subsequently requires InvalidOperationCommand. Start, fold, reconciliation,
runtime/legacy adapter calls and legacy writes must remain empty. The queued
start denial is unused because translation fails first.

Actual [private intent projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
checks requested node deliveries before issuing a start command. The selected
[delivery admission helper](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_authorities.py)
requires exact requested/admitted values, matching authority/workspace, a single
active matching registration and equal delivery material. That explains this
empty-admission rejection. The test replaces in-memory material; it does not
withdraw a persisted registration, refresh a database snapshot, mount a socket
or prove concurrent revocation behavior.

## Fresh execution, existing attempts and selection budget

The method named live_start uses a queued NewlyStarted and synthetic successful
RuntimeEffectResult. It asserts one exact StartEffectAttempt with the expected
intent/fingerprint, one adapter request whose effect ID and source intent-event
ID equal the returned start event, equality of request-derived intent with issued
intent, and equality of realization.intent_event with that original event.
One fold command must carry the same result and the public outcome-derived
transition/failure. Reconciliation, legacy dispatch/writes and forbidden effects
stay empty; effects_attempted is one. Despite its name, this does not prove that
a start committed before dispatch or that a fold became durable.

The existing-attempt method queues a direct terminal attempt while retaining the
ready pinned schedule. It requires one start lookup, one reconciliation command
with request-a and the exact attempt identity, no runtime/direct-fold calls and
one effects_attempted. Its ExistingFold comes from the recording reconciliation
service; no observer or actual reconciliation transaction runs. Thus it proves
the returned ExistingAttempt selects reconciliation, not how real terminal
journal classification behaves before selection.

The two-iteration budget test queues NewlyStarted followed by ExistingAttempt.
It requires two start calls, one runtime call, one reconciliation call and two
effects_attempted with no legacy writes or forbidden effect calls. The pinned
context permits this synthetic sequence. This establishes that selected loop
iterations consume budget independently of provider calls, not progression
through two different persisted activities or completion of a run.

## Start-result identity and provider uncertainty

Eight start-result cases combine NewlyStarted/ExistingAttempt with foreign run
identity, request fingerprint, worker/generation fence or forged original-event
correlation. The first three expect the fixed start-result-invalid conflict;
the constructor-bypassed event record expects the service-result-invalid
conflict. Each case checks the issued intent/fingerprint and one start call,
then requires no runtime, fold, reconciliation, legacy writes or forbidden
effect calls. This is admission of returned service values, not tests of the
start service's database constraints.

Four adapter cases cover a raised RuntimeError, legacy-result wrong arm, wrong
effect ID and a correctly typed prebuilt interpreter uncertainty. Each requires
one start/runtime/direct-fold call, no reconciliation and one attempted effect.
The fold's runtime result must have exact RuntimeEffectResult type, UNCERTAIN
kind, the start event ID, fixed provider-result-unknown code and exact categorical
boundary/reason details. The prebuilt uncertainty must be preserved by equality;
the exception canary must be absent from repr of the result. Final coordinator
status and durable outcome rows are not asserted.

The inspected coordinator catches ordinary adapter Exception, normalizes wrong
result type/identity and builds direct uncertain evidence. The runtime dispatcher
has a similar inner interpreter boundary, but this test supplies its already
constructed result rather than invoking a throwing interpreter. The matrix does
not establish behavior for every BaseException, all result fields, arbitrary
payloads or complete log/transport redaction.

A separate recovered-result case supplies an ExistingAttempt bearing a recovery
decision. It requires the explicit-recovery-authority conflict before provider,
reconciliation or direct fold. This prevents treating that returned recovery
record as ordinary forward selection authority; it is not a recovery workflow
or a claim that every prepared terminal history must be rejected.

## Named service failures versus raw internal faults

The error matrices inject NotFound, Conflict and Denied for each of start, fold
and reconciliation. They require corresponding coordinator exception categories
and exact fixed boundary messages. The inherited assert_safe_error requires
absent cause/context, combined str/repr length at most 256 characters and absence
of any supplied canary. These are concrete selected error-surface checks; calls
that supply no canary still check the chain and combined length.

One RuntimeError per service is separately required to escape as the identical
object. Recording services raise that exact class, so these are real injected
exceptions rather than malformed returned values. The source handles named
service failures categorically and leaves unexpected internal exceptions raw.
No public transport sanitization or safe exposure of arbitrary raw exception
messages follows from this contract. These matrices do not establish rollback,
receipt persistence or a complete no-effect assertion for each boundary.

## Exact result admission before hostile object hooks

Local hostile_copy creates subclasses whose __getattribute__ and __eq__ append
to a dispatch list and raise. It copies lawful dataclass fields with
object.__setattr__. Other helpers bypass constructors to place these hostile
state/event values, or an unrelated hostile attempt, inside otherwise exact
start/fold result types. capture catches any BaseException so the assertions can
distinguish the intended conflict from a hostile hook firing.

The start matrix has seven selected shapes: one hostile outer NewlyStarted,
then unrelated attempt, hostile state and hostile original event for both start
variants. Fold and reconciliation each have one hostile outer result and hostile
state nested in each fold-result variant. Every case requires zero hostile
dispatches, exact ExecutionCoordinatorConflict and the fixed service-result-invalid
message with safe-error checks.

Start rejection must precede runtime/fold/reconciliation calls. Direct-fold
rejection happens after one runtime call and one fold call, with no reconciliation.
Reconciliation rejection requires one reconciliation call with no runtime/direct
fold. All require no legacy writes. Those different points matter: rejecting a
malformed fold result does not undo an already invoked provider.

Actual coordinator admission checks exact outer types and reconstructs
[attempt records](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
before comparing issued coordinates. Selected record checks reject non-exact
state/event components before traversing their fields. The
[fold-result constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
reconstruct and validate nested attempt/outcome records. These checks explain
the selected adversarial cases. The matrix is not exhaustive over every nested
field, every outer variant or arbitrary malicious Python objects.

A separate eight-case matrix uses constructor-valid foreign fold results:
identity/fingerprint drift, both NewlyFolded/ExistingFold variants, and both
direct-fold/reconciliation boundaries. It asserts a real mismatch against the
issued command or selected existing attempt as well as the same safe categorical
conflict and boundary-specific call counts. This distinguishes foreign but
internally coherent results from malformed/hostile values.

## Root ownership and exact source-surface policies

The ownership test requires the 14 coordinator names to be present in the
Operations root __all__, not equality with the entire root export surface. It
requires the private intent projection to be absent both from root __all__ and
as a root attribute. For the two named inventory rows it compares declared
exports, internal dependencies and protecting-test sets to local expectations;
runtime_effects declares only runtime_effect_request_for_context as its canonical
public export. It does not validate every inventory row, root binding identity
or duplicate-row uniqueness: its dictionary selection would collapse duplicate
module keys.

INVENTORY_PATH comes from CPK_PACKAGE_MODULE_INVENTORY or the repository-relative
default. Source paths for coordinator.py and runtime_effects.py come from this
test's package location. The inspected harness mounts the inventory and pinned
architecture-testing source read-only and supplies the override. These paths
are test inputs, not proof that the complete packaging/CI pipeline ran here.

The file's _exact_imports builds ImportSurfaceEntry tuples. _exact_calls expands
each target/count into repeated resolved targets, or an unresolved target for
None. The tables therefore preserve occurrences rather than merely allowing a
set of names. Runtime translation explicitly expects one unresolved call; its
policy is not a claim that every call target is statically known.

The architecture canary analyzes a two-line aliased sample import/call and
requires no findings for matching policies. Two later tests analyze the complete
coordinator and runtime-effects source text and require no findings against the
explicit import/call tables. This file does not perturb either surface to test
that each mismatch is detected; the shared architecture package owns those
evaluator laws. The long tables include ordinary constructors, helper calls,
store calls and builtins, not just effects or public Core calls.

At the inspected clean architecture-testing checkout
7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef, analyze_source parses text with stdlib AST
and projects imports, aliases and lexical call targets. Alias qualification uses
name/attribute syntax; it is not dynamic object resolution or scope-sensitive
execution tracing. Policies compare sorted occurrence projections with canonical
expected tuples. Locations and call arguments are absent from the projected
call comparison; changing an argument or moving a call can preserve the surface,
while adding a harmless call or import changes it. Neither order of execution
nor reachability is established.

These exact tables make ownership drift visible, at the cost of refactor
sensitivity. They must be reviewed with behavioral tests and source meaning;
updating counts until a test passes does not establish that a changed interpreter
boundary is sound. The title about private projection using public Core algebra
should be read as a specific whole-module policy, not a proof that every call is
to Core, that the package graph is acyclic or that implementation semantics are
complete.

Read depth: full 1,844-line suite/all 21 methods, all four complete import/call tables
and local helpers; freshly reread full coordinator fixture509, imported context/
graph/product/delivery helpers, inherited selected safe-error/identity/evidence
helpers; selected actual dispatch, private intent/delivery admission, attempt and
fold-result validation, root imports/inventory and harness wiring. Fresh shared
architecture exports and selected AST/alias/call projection and policy evaluation
were read at the clean pinned checkout. Retained actual coordinator admission,
effect loop, error translation, classification and PostgreSQL service/UoW context
informed the limits; this is not a fresh full audit of every imported module or
all of coordinator.py/runtime_effects.py. No coverage inventory or application
source changed. The companion adds no security surface and authorizes no tests,
database/provider actions, recovery, compensation or held live work.
