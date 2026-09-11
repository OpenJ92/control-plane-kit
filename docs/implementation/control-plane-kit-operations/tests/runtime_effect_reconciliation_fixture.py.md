Source: [control-plane-kit-operations/tests/runtime_effect_reconciliation_fixture.py](../../../../control-plane-kit-operations/tests/runtime_effect_reconciliation_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 109-line fixture exposes reconciliation language/service bindings, checks
their presence and builds a nominal reconciliation command. It inherits
[EffectAttemptStartFixture](../../../../control-plane-kit-operations/tests/effect_attempt_start_fixture.py)
for identity, worker authority, lease fence and error assertions. Despite its
runtime-effect filename, its actual owners are effect_attempt_reconciliation and
effect_attempt_reconciliation_interpreter. It has no observer implementation,
observation-result builder, service factory, database setup or reconciliation run.

_load_optional imports the requested module and returns None only when a caught
ModuleNotFoundError names exactly that module. A missing transitive dependency,
partial ImportError or other failure escapes. Both owner modules are loaded
through this helper, and exported names are captured with getattr defaults of
None. This permits explicit missing-publication assertions without masking
unrelated import failures. It neither retries imports nor manufactures a working
replacement implementation. The injected import_module argument supports focused
consumer tests of this behavior.

require_language checks the presence of ReconcileEffectAttempt, RuntimeEffectObserver
and four reconciliation error classes. require_service checks only that the
service binding is not None. These override the parent's start-language/service
checks; they do not inspect signatures, root-export identity, callability, protocol
conformance or runtime availability. Those assertions belong to consuming tests.
maxDiff=None only changes unittest failure presentation.

command first requires the reconciliation language, then supplies request-a,
EffectAttemptIdentity(RunId("run-a"), "start-runtime", 1), worker-a with
EXECUTION_OPERATE and a worker-a/generation-seven ExecutionLeaseFence. Keyword
changes replace these defaults before the actual ReconcileEffectAttempt constructor
runs. Changing one member does not recompute the others, so consumers can exercise
incompatible authority/fence or coordinate combinations. Unknown keyword names
reach the constructor rather than being filtered by the fixture.

The command carries no intent, effect payload, observation, recovery decision or
runtime registration. The inherited intent/transition helpers remain available,
and importing the parent also computes its synthetic default intent fingerprint,
but this overridden command method does not call them. It replaces the parent's
start-command builder entirely, including that builder's compatibility/fallback
construction logic. It always calls ReconcileEffectAttempt directly.

The actual [command owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation.py)
defines a frozen four-field value. Admission requires exact command, identity,
RunId, authority, fence and scalar types; bounded command coordinates use a
512-byte UTF-8 limit and reject control characters and invalid Unicode encoding.
It reconstructs identity, authority and fence, requires equality with their
inputs and requires authority/fence worker IDs to agree. Invalid admission raises
the fixed InvalidOperationCommand message. It does not require EXECUTION_OPERATE
at construction: that is the service's later policy check. Shape admission proves
neither an existing attempt nor a current claim or unexpired lease.

RuntimeEffectObserver is a Protocol with observe(request, authority), where the
request is RuntimeEffectObservationRequest, authority is a supplied
RegisteredRuntimeAuthority or None, and the result is RuntimeEffectObservationResult.
Its read-only description is an implementation obligation, not enforcement by
this fixture or the protocol annotation. No observer instance is constructed or
invoked here. The four error bindings likewise provide category vocabulary,
not error handling performed by the fixture.

The actual [observation contract](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
wraps an exact RuntimeEffectRequest, derives its intent and fingerprint and checks
grant/connection-admission context. Its result union has six distinct variants:
observed succeeded, failed, absent, conflict, indeterminate and observer unsupported.
The request-for-intent function binds the original effect ID and transient grants
to the intent. These contracts explain the observer signature; the reconciliation
fixture creates none of these request/result values, asserts no observation law
and supplies no provider evidence.

Selected actual [interpreter paths](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
revalidate the command and scope, inspect durable request/run/attempt authority
and return existing fold truth for a non-started attempt. Its fresh path validates
lease, stored intent and required registration, leaves that initial unit-of-work
context, builds the observation request and invokes the injected observer. It then
admits the returned observed outcome, constructs a guarded fold and delegates to
execute_observed, translating selected fold failures. This is context about the
owner, not execution or proof of those boundaries by the present fixture. An
arbitrary exception from the observer invocation is not caught at that call by
the inspected fresh path; protocol return annotations do not normalize failures.

Synthetic observer behavior resides in consumers. The separately inspected
[PostgreSQL reconciliation fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_reconciliation_fixture.py)
defines RecordingObserver, which can reject invocation under its active ledger,
records request/authority and then raises a supplied error or returns a preselected
result. Its FailIfObserver records and raises a sentinel. The
[interpreter contract tests](../../../../control-plane-kit-operations/tests/test_effect_attempt_reconciliation_interpreter_contract.py)
define another observer sentinel that counts and raises. None performs provider
inspection merely by returning a lawful observation value, and none is implemented
or installed by RuntimeEffectReconciliationFixture itself.

Selected [language consumer tests](../../../../control-plane-kit-operations/tests/test_effect_attempt_reconciliation_contract.py)
check nested/partial import failure propagation, command shape/root identity and
the observer's exact parameter/type-hint contract. They also obtain observation
stories from a separate outcome fixture. The inherited safe-error helper checks
empty cause/context, combined str/repr at most 512 characters and omission of
supplied nonempty canaries; this fixture does not call it automatically when
command construction fails. There is no general redaction or exception wrapper
around command or import operations here.

The exports include the fixture, captured command/protocol/error/service symbols,
module-name constants, loader and loaded module objects. They supply test wiring,
not authorization, registration, provider access or a durable lifecycle. There
are no test methods, transaction/cleanup hooks, history records or retry/resume
behavior in this file.

Read depth: full 109-line fixture and 208-line parent; full reconciliation language
owner; selected actual service entry/fresh observation/fold paths, core observation
request/result/intent binding contracts and consumer import/protocol/observer
helpers. The reconciliation interpreter and consumer suites were not fully
reviewed for this note. No tests, application imports, database connections,
source/dependency changes, credentials, Docker or provider actions were executed
while authoring this companion.
