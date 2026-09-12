Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

EffectAttemptFoldService interprets one already-started effect-attempt transition
into durable event, optional outcome/observations and attempt truth. It does not
start or redispatch an effect, choose recovery, resolve credentials, contact a
provider, advance a graph or approve an action. The caller supplies a unit-of-work
factory and keyword-only ID factory. Operations owns these durable relationships;
the [Core fold](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
owns the pure transition algebra.

execute accepts the exact FoldEffectAttempt language. execute_observed accepts
GuardedObservedEffectFold and passes its fold plus protected intent/authority
context into the same transactional path. The
[language owner](effect_attempt_fold.py.md) validates exact nested values and joins
outcome, attempt, request, original event and optional runtime-authority reference.
It does not itself authorize current database state. Ordinary execute rejects an
ObservedEffectOutcome after locked truth/claim checks, even on replay; callers
must use the guarded entry for that profile.

Before opening a unit of work, both entries require EXECUTION_OPERATE and translate
the command's lease fence into the Core effect fence. Translation admits exact,
nonblank, UTF-8-encodable worker text of at most 256 characters without NUL and an
exact generation integer from 1 to 2**63-1. The command has already imposed its
own stricter text/type relationships. This is capability and value admission,
not HTTP authentication or proof that the request claim is still current.

Inside the unit of work, the service obtains request, request-scoped run and attempt
with update locks in that order. It checks request ID, run ID, run admission's
request, run/request plan agreement and attempt identity. It does not select the
latest run or check run completion status here. The request must be CLAIMED with
a claim fence equal to the supplied command fence. Record decoding and concrete
lock behavior belong to the adapters; this service uses duck-typed stores rather
than revalidating every request/run field against an exact record class.

Current claim ownership and historical attempt ownership are separate:

- Direct execution against STARTED requires the exact historical effect fence.
- Direct terminal replay or guarded observation permits a greater generation;
  equal generation requires the same historical worker, and a previous recovery
  decision disallows a direct transition.
- RECONCILED/ABANDONED likewise require nondecreasing generation and the same
  historical worker at equal generation. The pure fold then checks recovery
  evidence and transition legality; this is not permission to invent a decision.

The service deliberately calls Core with the attempt's historical fence after
checking the current command authority. A newer lawful claimant can report or
replay under those rules without rewriting the original fence. Core determines
whether the result is exactly the current state or a new state. Core contract
errors become the fixed incongruent conflict; the service does not repair them.

Fresh and replay admission have different purposes:

| Path | Lease observation | Active runtime authority |
| --- | --- | --- |
| Exact replay | No resampling | No registry reload |
| Fresh ordinary execution/recovery fold | Sample and compare locked request; expiry alone does not deny | No guarded registry check |
| Fresh guarded observed fold | Sample and compare request; expiry denies | Reload and compare when an authority reference is supplied |

Every path still passes command validation, current claim equality and historical
fence rules first. Fresh paths load stored intent evidence and match attempt,
original start event, request and request fingerprint. Guarded fresh paths also
require equality with the caller-supplied protected intent record. A referenced
runtime authority is fetched active-for-update in the same workspace using the
persisted intent reference. Missing/revoked authority denies; malformed returned
type/kind/status conflicts; a different otherwise typed registration denies.
No-reference guarded input requires no registration at language admission.

The PostgreSQL lease observer reacquires the request, samples clock_timestamp and
computes expiry. The sampled request must equal the earlier locked request. Its
time supplies the fresh transition event, not a provider timestamp. There is no
second expiry sample after planning or immediately before commit. Ordinary folds
of an expired but still-current claim are intentionally different from fresh
guarded observations. Replay does not promise renewed authority or freshness.

On exact Core replay, recorded failure must equal the command failure, except for
the narrowly recognized legacy direct-outcome failure shape. That compatibility
path derives the two-field profile/fingerprint details from the actual outcome,
requires the command's current canonical failure and accepts only exact recorded
legacy equality. It does not rewrite legacy events or accept arbitrary near-misses.
For a direct outcome, the store reloads the exact identity/latest-event aggregate;
the service checks exact record type, workspace, attempt and outcome equality and
constructs ExistingFold. Recovery replay has no direct outcome record.

Replay skips fresh intent loading at this service layer, lease observation,
runtime-authority lookup, ordinal/ID allocation and all explicit event/outcome/CAS
writes. It is not a zero-query or transaction-free path: initial locks, aggregate
reads and unit_of_work.commit still occur. The actual
[outcome store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
reconstructs bounded persisted membership; HTTP verification membership can itself
read intent evidence. Thus no service-level fresh-intent call is not a promise
that all nested adapters avoid intent reads.

A fresh fold allocates the next run event ordinal and builds the complete result
before its first explicit event write. _plan_result requests one event ID plus
one ID per endpoint observation; values must be exact strings and mutually unique.
Record/projection constructors impose the remaining validity rules. The original
start event is preserved, the new event contains an attempt/state fingerprint,
and outcome evidence is projected into typed observation records with protected
intent context. There is no provider query in this projection. ID generation can
fail or consume IDs before a transaction rolls back; there is no ID retry or
reclamation. Factory failures outside the selected record-error catch escape.

The event-kind table has fourteen ordinary/compensation combinations: four direct
statuses and three recovery resolutions in each family. Family comes from the
original start event. Recovery outcomes create the recovery event/attempt without
a direct outcome aggregate. Unsupported combinations conflict. An UNCERTAIN
record is valid durable uncertainty, not successful realization or settlement.

Fresh writes are ordered event append, zero or more observation puts, optional
outcome insert, then attempt compare-and-set. Each acknowledgement must have the
exact expected record type and equal the planned value; a mismatch stops later
steps and raises changed-concurrently conflict. The PostgreSQL CAS compares the
complete prior stored state, not just its identity. Acknowledgement equality is
not an independent readback of every written row. The adapters and shared
transaction supply persistence, ownership/FK and rollback behavior.

Both fresh and replay paths request commit and return within the context manager.
With [PostgresUnitOfWork](postgres/unit_of_work.py.md), commit() only marks intent;
physical commit occurs during successful context exit before the caller receives
the result. Exceptions cause rollback/close through that owner. No provider call
is enclosed by this transaction. The generic factory must supply the promised
transactional stores; this service cannot make arbitrary fake adapters atomic.
Physical commit failure may remain ambiguous, and rollback/close failure can mask
an earlier error. This module neither retries nor certifies rollback after an
ambiguous commit.

Error translation is deliberately local, not a blanket catch. Missing locked
request/run/attempt becomes NotFound; selected record/value decoder failures
become invalid-truth Conflict. Fresh missing/invalid intent conflicts. Missing
active authority denies, registration errors conflict, and incongruent replay
or changed acknowledgements conflict. Selected catches raise fixed text outside
their handlers, avoiding raw causes/contexts for those paths. Unexpected adapter,
ID-factory, attribute, transaction or cancellation failures may escape unchanged;
there is no universal redaction or primary-error preservation guarantee. The
lease-observation helper does not catch a missing-row KeyError. Boundaries beyond
this service must not expose arbitrary internal exceptions as public reports.

The [interpreter-contract tests](../../tests/test_effect_attempt_fold_interpreter_contract.py.md)
cover entry/scope/fence admission before a deliberately failing factory, not SQL.
The [first/replay tests](../../tests/test_postgres_effect_attempt_fold_first_replay.py.md)
and [rollback tests](../../tests/test_postgres_effect_attempt_fold_rollback.py.md)
exercise selected durable paths and fault seams; their companions distinguish
new connections from restart, pre-call faults from changed returns after writes,
and selected snapshots from complete database/provider truth. Guarded tests add
runtime-authority and observation-specific laws. None grants recovery policy,
provider authenticity or a new live acceptance claim.

Read depth: full 567-line interpreter including all helpers/event mappings; retained
full language, parent fixture and interpreter/atomic/guarded language tests;
refreshed actual Core fold/duplicate rules, complete PostgreSQL UoW, and selected
request/lease/ordinal/event, intent, outcome/membership, CAS, authority and test
paths. Supporting stores/tests are selected dependency context, not new full-file
review credit. No imports/tests, Docker, database/provider access or source edits
accompanied this documentation.
