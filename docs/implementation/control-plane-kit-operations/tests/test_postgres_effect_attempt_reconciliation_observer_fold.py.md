Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_observer_fold.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_observer_fold.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six PostgreSQL tests cover synthetic observer results passed through actual
reconciliation and guarded folding, remote connection admission, selected result
correlation failures and exception boundaries. The
[reconciliation fixture](postgres_effect_attempt_reconciliation_fixture.py.md)
provides real test database setup, secret admissions and recording doubles. No
real runtime provider or secret resolver performs the represented observation.
The six observation variants are succeeded, failed, absent, conflict,
indeterminate and observer unsupported, each represented in ordinary and
compensation stories.

_RecordingFold appends every guarded command, then raises its configured error,
returns a configured override or delegates to the supplied service, in that
priority order. Its private sentinel distinguishes no override from an explicit
None result. Override/error cases do not run the underlying fold, even though
the test constructs a real service to pass into the wrapper. The wrapper neither
validates results nor compensates earlier authorization transactions.

The first control requires twelve observed stories and both compensation values.
For each it builds a STARTED record, an intent, an observation and an
ObservedEffectOutcome, derives transition/failure with production helpers, and
constructs FoldEffectAttempt. It checks that the command retains those exact
transition, failure and outcome values. This is constructor/projection consistency,
not an independent assertion of each story's declared status, transition or failure
mapping. persisted_intent also reads the fixture run's request/plan coordinates;
this is not an entirely database-free control despite constructing its candidate
attempt as a value.

The principal twelve-world matrix seeds a STARTED attempt and remote runtime
authority, enumerates required uses and registers the provider/references before
creating a ledger. It then uses RecordingObserver with that ledger and an actual
fold service wrapped by _RecordingFold. The command carries EXECUTION_OPERATE and
SECRET_PROVIDER_USE. Every world must return an instance of NewlyFolded, invoke
the observer exactly once and pass an exact RuntimeEffectObservationRequest with
the original effect ID and intent.

The grant reference/intent tuple must equal the production required-use tuple.
Every grant must carry worker-a and the expected operation, run, activity and
original effect coordinates. Observed authority must equal the seeded registration,
and connection admission must equal the registration's authority reference and
three TLS certificate/key references. The test compares these selected fields;
it does not independently derive the required-use set or compare every grant
registration, correlation or fingerprint field to a separately read durable row.

The recorded guarded fold must be invoked once and retain the exact intent record,
runtime authority and production-constructed ObservedEffectOutcome. The returned
outcome record must equal expected_outcome_record for the returned attempt and
chosen event ID. That fixture helper shares production
[outcome projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py),
including effect_outcome_observation_records and EffectAttemptOutcomeRecord; it
also accepts the actual returned attempt as input. This is not an independent
oracle for event time, ordinal, fold state or projection algorithms. It omits the
intent_record argument used by the real fold projection, which these synthetic
RuntimeEndpointObservation stories permit; it does not exercise the authoritative
HTTP VerificationCompleted projection branch.

The ledger assertion is specifically 1 + len(uses) entries, equal entries/exits
and zero active contexts. RecordingObserver rejects entry while that ledger is
active. The fixture would bind both reconciliation and its default fold service
to the ledger, but every service call in this file supplies a custom fold service.
Here fold_service_with_id_factory uses the inherited unwrapped self.unit_of_work,
so the recorded count includes only initial reconciliation reads and one context
per authorization. The actual guarded fold transaction is outside that ledger,
as are seed/admission transactions. This is a context-lifetime assertion, not a
measurement of all PostgreSQL transactions or physical connection state.

The actual
[reconciliation interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
exits its initial locked read context before authorizing uses, then calls the
observer after the individual authorization contexts have exited. The initial
context does not request commit, so the actual unit of work rolls it back and
closes it. The
[secret authorization service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
requires the use scope and canonical request time, locks correlation and active
reference/provider admission, verifies identity, prefix and intent bounds, then
reuses congruent evidence or inserts a new authorization. Each authorize_resolution
requests commit and returns its grant only after context exit performs the commit.
The grant contains pinned routing/reference evidence, not resolved secret bytes.

Provider registration and each reference registration also commit separately
during setup. Authorization commits are not part of the later fold transaction:
an invalid observation or failed fold can leave authorized-use evidence in place.
This file does not call authorization_rows or inject a partial authorization
failure to verify that persistence independently. Its final test reuses one seeded
world and observer across its subcases, allowing prior authorization evidence and
observer calls to accumulate; it does not assert a fresh admission row per call.

The actual
[guarded fold interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
revalidates command and current locked truth, intent, lease and active authority
before planning a new result. It allocates the event/observation IDs and validates
the planned result, then writes event, observations, outcome and attempt CAS with
exact acknowledgements in one commit-requested unit of work. The test supplies
deterministic Sequence IDs but does not retain or assert Sequence.calls or
exhaustion. It exercises the real commit path without separately reading back all
new fold rows or testing transaction rollback/commit failure here.

Only the principal matrix compares before/after non_advancement_snapshot. That
snapshot selects plan rows, request status/worker/generation, run state, workspace
pointers, graph versions and realized projections. It omits claim timestamps,
attempt/event/outcome/observation rows and secret/runtime-authority admission and
authorization tables. Equality establishes unchanged selected surrounding state,
while the fold is expected to advance attempt history. The multi-query snapshot
is not an atomic image of the whole database.

The process-empty remote case asserts empty top-level authority deliveries and
empty per-product runtime-authority deliveries. Products and the remote registration
remain; empty delivery tuples are not an empty-product or zero-secret-use request.
The actual required-use enumerator includes registered remote TLS references
independently of process-delivery declarations. This case requires a new fold,
one observation, matching intent/fingerprint/authority/connection admission and
grant-use tuple, one guard with the correct intent record, and the same limited
ledger count. A RuntimeEffectContractError is converted to a test AssertionError
with its cause. It adds no non-advancement snapshot or exact returned-outcome check.
The
[connection grant contract](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py)
validates supplied grant uses and coordinates; it admits partial grant tuples
structurally, so construction alone is not a connection-completeness proof.

Two observation-correlation faults replace either the effect ID or request
fingerprint with a valid-shaped foreign value. Each must call the observer once
with the registered authority, raise the fixed invalid-truth reconciliation
conflict with no cause/context and leave the retained fold call list empty.
These rows directly establish rejection before folding, but do not compare
authorization persistence or unchanged history after failure.

The observer-admission test returns None or object() and requires that same fixed
conflict without cause/context. It then raises TypeError or RuntimeError from the
observer and requires the identical error object to escape. These cases do not
retain/assert fold call lists, and they are not an exhaustive exact-type or forged
observation matrix. Source places the observer invocation outside outcome-error
normalization, explaining the raw propagation.

The final test overrides the fold result with None/object() and requires a fixed
invalid-truth conflict without cause/context. FoldConflict maps to the fixed
incongruent reconciliation conflict; FoldDenied maps to fixed authority denial;
both discard cause/context. Raw TypeError/RuntimeError must retain object identity.
Despite the test's fault-identity name, identity preservation is asserted only for
those raw errors. Actual source also maps FoldNotFound to an incongruent conflict
and admits exact NewlyFolded or ExistingFold result types; this test neither
injects FoldNotFound nor tests forged/subclass typed results or an ExistingFold
override. No result/exception row asserts wrapper call count or an after-snapshot.

Read depth: the complete 400-line source, six tests and recording helper were read,
along with the complete reconciliation fixture and relevant inherited story,
intent, ID, expected-outcome and snapshot helpers. Selected actual reconciliation,
authorization/admission/grant, guarded-fold, outcome-projection, connection and
unit-of-work paths were cross-checked. This is not full-owner or adjacent-suite
coverage. Validation of this companion was limited to links, whitespace and
frozen-source comparison; no application imports, tests, database/provider calls,
credential access, inventory changes or publication were performed.
