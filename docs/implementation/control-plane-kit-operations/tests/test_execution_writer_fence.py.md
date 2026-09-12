Source: [control-plane-kit-operations/tests/test_execution_writer_fence.py](../../../../control-plane-kit-operations/tests/test_execution_writer_fence.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests protect the language through which post-claim writers carry an
ExecutionLeaseFence and a selected coordinator source-structure rule. The fixture
constructs worker-a authority with EXECUTION_OPERATE scope, a generation-seven
fence and one IdempotencyKey. It creates no claim, database connection or runtime.
Command construction proves input consistency, not authenticated authority or
agreement with a currently stored lease.

For Start, Pause, Resume, Complete, Fail and CancelActivityRun, the first four
dataclass fields must be run_id, authority, fence and idempotency_key in that
order. The assertion allows command-specific trailing fields. All six successful
examples must retain the exact supplied fence object; pause/completion/cancellation
use bounded evidence and failure uses typed terminal failure evidence. A foreign
worker fence must raise InvalidOperationCommand for each command, with the exact
message "authority and fence must agree", no cause/context and no worker canary
in repr. This file does not apply a general length-bound error helper.

The actual [lifecycle owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
defines frozen command dataclasses whose common validator checks RunId, typed
authority/fence/key and worker equality. Fence and authority admission use
isinstance; the test title's exact congruence concerns matching worker values,
not exact-type rejection of subclasses. Their
[fence value](../src/control_plane_kit_operations/execution_leases.py.md) validates
the worker/generation pair itself. The tests do not separately exercise malformed
run IDs, wrong fence types, hostile subclasses, evidence rejection or missing
scopes. The fixture's authority is data, not a credential-verification result.

The fingerprint test compares two otherwise matching StartActivityRun values at
generations one and two and requires different private _fingerprint results.
The inspected implementation includes claim_generation in the shared run intent,
then hashes sorted compact JSON with SHA-256; command-specific evidence/failure
is added where applicable. This test protects generation sensitivity for Start,
not exact digest bytes, every other command's fingerprint, collision freedom or
database idempotency replay behavior.

ExecuteActivityRun must have exactly run_id, authority, fence, idempotency_key and
max_effects fields. A valid command retains the same fence; a foreign worker
raises the same categorical mismatch message without the canary in repr. Unlike
the six lifecycle negative cases, this assertion does not inspect exception cause
or context. ActivityRealizationContext is inspected only for the presence of a
fence field: the test neither constructs a realization context nor exercises its
validation, field order or complete shape.

The actual [coordinator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
defines both values as frozen dataclasses. ExecuteActivityRun checks worker/fence
agreement and a positive exact-int max_effects. ActivityRealizationContext also
checks the fence against the supplied request claim and binds request, run, plan,
graph projections, workspace-scoped material and intent event. Its factory passes
the context fence onward. These are inspected source contracts, not additional
assertions supplied by this test. Frozen fields do not by themselves prove deep
immutability or freshness of the represented durable state. This file contains
no mutation-attempt or dataclasses.replace test.

The provider-boundary test inspects RuntimeEffectRequest fields and excludes the
three names fence, claim_generation and worker_authority. The operations
[runtime-effects module](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
imports this value from its actual
[core owner](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py).
That frozen request still carries runtime authority references, deliveries and
secret-resolution grants; absence of an execution fence does not mean absence of
all authority. The selected coordinator secret-authorization path uses replace
to return a request with a grant tuple, preserving the original value and invoking
the request constructor's validation. This is source context only: the test does
not call that path, authorize secret use, inspect nested descriptors or prove
that arbitrary provider payloads cannot contain execution metadata.

The last test obtains source for _load_context, _record_step_event and
_record_outcome, dedents/parses each method and counts ast.Call nodes whose callee
is an ast.Name. Each must contain exactly one call named _locked_request_and_run.
It does not execute those calls, resolve their runtime binding, establish that
every control-flow path reaches them or inspect SQL locks. Attribute calls and
other writer methods are outside this exact syntactic assertion.

The inspected shared guard first reads a run locator, then obtains the request
and run through their for-update store methods, rechecks linkage and checks
EXECUTION_OPERATE plus CLAIMED state, claim presence, exact fence equality and
worker agreement. It does not compare lease expiry with a clock. The three
methods call it inside their units of work; step/outcome writes also require a
RUNNING run and put claim_generation into event evidence before committing.
_load_context reads pinned plan/graph material and history before deriving a
schedule. These source observations explain the guard's purpose, while actual
lock acquisition, stale-writer races, rollback and persisted history require
other tests with real stores.

Security evidence here is command congruence, selected safe mismatch errors,
generation-sensitive intent and separation of execution writer authority from
the provider request's top-level fields. No authentication, live lease freshness,
provider mutation, restart, cleanup or transaction-concurrency claim follows from
these pure and AST assertions.

Read depth: all 185 source lines and helpers; selected actual lifecycle command,
validation and fingerprint paths; coordinator command/context, three inspected
writer methods, shared guard and request replacement path; actual core runtime
request definition/validation and workflow key/error definitions. The fence owner
was fully read in the preceding authority review. Documentation authoring did not
execute tests/imports, access credentials or invoke database/provider operations.
