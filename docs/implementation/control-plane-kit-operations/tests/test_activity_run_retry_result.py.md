Source: [control-plane-kit-operations/tests/test_activity_run_retry_result.py](../../../../control-plane-kit-operations/tests/test_activity_run_retry_result.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These ten tests protect the pure
[ActivityRunRetryResult](../src/control_plane_kit_operations/activity_run_retry.py.md)
contract. Every request, run, event and action is constructed in memory. They test
record correspondence and selected error representations; there is no database,
retry service, lease observation, persisted journal replay or external effect.
The module import guard masks absence of only the exact owner module while
re-raising unrelated missing dependencies.

The fixture builds a CLAIMED request with worker-a generation 7, a FAILED started
but unsettled prior run, and a distinct successor with the next attempt number
and prior-run metadata. A retry decision event belongs to the prior run; a
RUN_OPENED event at ordinal one belongs to the successor. Their recovery evidence
preserves the request fence. Decision/opened times, successor creation and action
time all use the same observed string. Other times are placeholders such as
claimed-before and settled-after, not real canonical database clock observations.

The action is RECORD_RECOVERY_DECISION in the request session, with a constructed
13-key payload linking all identities/counters/events and recovery evidence, an
idempotency key and a fabricated lowercase-hex fingerprint. Request operator,
retry action actor and claim worker are deliberately different fixture roles.
No originating RetryFailedActivityRun is supplied to the result, so this test
cannot prove actor or fingerprint correspondence with a submitted command.
Selected actual [record constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
provide local run timing, retry identity and event shape laws used by the fixture.

The public-shape test requires owner/root class identity, a frozen dataclass and
the exact seven fields. It compares the full descriptor, including compact event
coordinates, action identity, recovery descriptor and replay flag. Selected
authority/scope/claim-time/secret/endpoint strings must be absent from descriptor
repr. This assertion does not inspect result repr or an arbitrary serializer;
the result still contains full nested records. Frozen wrapper admission is not
proof that every nested Mapping is copied or immutable.

Two tests reject an outer result subclass and subclasses of each of its six
record fields. They construct real subclass instances and require
OperationsRecordError on result construction. The error helper requires no
cause/context, a combined str/repr length at most 512 and absence of supplied
canaries. These exact wrapper checks do not recursively test every nested record
or metadata implementation for hostile behavior.

The lineage test supplies 16 mutations spanning request/plan/session identities,
queued request state, fence worker/generation, prior run identity/plan/admission/
status, successor identity/plan/admission/prior metadata and non-bool replayed.
Each must be rejected, followed by a separate run-ID canary case. These are selected
single-field drifts with the rest of the result retained; they do not prove a full
database lineage graph, availability of the prior run or every combination of
invalid record fields.

Metadata tests check initial prior-run metadata against four missing/changed/extra
forms and later-attempt prior metadata against five forms. Successor metadata and
opened-event evidence each receive five missing/changed/extra candidates. The
source compares the resulting dictionaries with the expected retry metadata;
the tests do not define a separate byte-canonical encoding or enumerate Python
numeric/bool equality corner cases. Their completeness concerns selected keys and
values, not an independent recursively typed wire format.

The event/time test applies 13 mutations: successor creation time, foreign decision
run/recovery, decision ID/recovery kind/ordinal/time, opening run/ID/ordinal/kind/time/evidence
and action creation time. Many leave the original action payload unchanged, so
rejection can arise from its cross-record comparison as well as the event-specific
checks. A stronger distinct-ID case changes both the opened event ID and its
payload reference to equal the decision ID; it must still fail. An additional
event-ID canary must not leak through expected errors. No actual event ordering
or next ordinal is allocated against a journal here.

The maximum-attempt test rejects a skipped counter (prior two, successor four),
accepts MAX_ATTEMPT-1 followed by MAX_ATTEMPT, and rejects RetryIdentity above the
PostgreSQL integer maximum. The last assertion is on the imported record type,
not a failed live retry operation. The pure command contains no current counter;
the interpreter must read and enforce capacity before creating durable work.

The replay-status test explicitly lists all ten current ActivityRunStatus values
and asserts its tuple equals the enum. For each it builds timing-compatible records
and requires replayed=True construction to preserve the supplied status. Every
non-CLAIMED status is rejected for a direct result. This checks representation of
an evolved successor, not the sequence of transitions or effect history that
produced that status. It does not imply that terminal status authorizes another
retry or that the claimed request's lease remains active.

Action validation has six identity/shape mutations: wrong session/kind, missing
or oversized idempotency key, missing or uppercase-hex fingerprint. For each of
the 13 expected payload keys it tests both omission and a changed value, then
rejects an extra key and a run-ID canary. The actual result compares dict(payload)
with the complete expected dictionary and checks fingerprint shape; it does not
recompute the fingerprint from a command. This file does not mutate action actor
or test semantic command-to-action authorization. Those checks live in the
[interpreter replay path](../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py).

The tests use actual owner and record constructors with no mocks replacing the
result validators. They nevertheless supply synthetic coherent evidence, including
arbitrary well-shaped digest text, rather than loading retained truth. They do not
exercise lookup failure, row locks, concurrent retries, commit/rollback, successor-
chain reconstruction or provider/adapter ambiguity. Expected-error bounds are
selected validation evidence, not universal log or exception sanitization.

The [command/record tests](test_activity_run_retry_contract.py.md) separately cover
the command fingerprint, nominal inputs, imported counter/fence laws and ownership.
Read depth: full 671-line result test with every fixture/helper and ten methods,
full 299-line owner and 505-line neighboring contract test retained, plus actual
record/authority/fence and selected interpreter context. No source/pin changes,
executable tests, database setup, credentials/private-key access, provider/runtime
actions or publication occurred. Documentation adds no security surface and makes
no claim that these tests ran or passed during authoring.
