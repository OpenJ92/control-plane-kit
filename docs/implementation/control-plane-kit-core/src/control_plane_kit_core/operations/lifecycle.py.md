Source: [control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py).
Maintain this document alongside its source file. Recheck constructor admission,
canonical factory defaults, descriptor vocabulary and Operations consumers when
changing lifecycle laws.

This module owns pure admission, run, event, recovery and graph-advancement
contracts. Its five frozen dataclasses describe timing, event shape, recovery
preconditions, public lifecycle operations and a complete contract set. Closed
enums name request/run statuses, event kinds and scopes, failure categories,
recovery scopes/decisions, operation kinds and enforcement owners. It neither
stores a run nor interprets an operation into a transaction or provider effect.

## Values and transformations

```text
closed enums + validated contract records
  -> complete, sorted ExecutionLifecycleContractSet
    -> descriptor mapping
      -> exact-key decoding and constructor validation

canonical_execution_lifecycle_contract_set()
  -> one intended record per status, event, recovery kind and operation kind
```

The factory and constructors have different strength. The factory supplies the
intended full policy; constructors enforce the particular restrictions below.
Request/response schema names are strings, not instantiated request classes.
Stage and role vocabulary comes from the
[service boundary](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py).

## Timing and event shape

RunStatusTimingContract requires a typed status and exact booleans matching the
module's started/settled tables. CLAIMED requires neither timestamp; CANCELLED
requires settlement but no start; RUNNING, PAUSED, FAILED and COMPENSATING require
start without settlement; SUCCEEDED, COMPENSATED, PARTIALLY_FAILED and
UNCOMPENSATED_FAILURE require both. These flags do not validate a timestamp or
establish that a run reached that state.

ActivityEventContract requires the scope returned by activity_event_scope, whose
explicit step-event set maps to ACTIVITY and whose remaining enum values map to
RUN. Its descriptor derives requires_activity_id from scope; decoding rejects
an inconsistent supplied flag. Recovery permission may be true only for
RECOVERY_DECISION_RECORDED, but construction does not require it to be true for
that kind. Failure permission is an exact boolean without a per-kind constructor
matrix. The canonical factory supplies the narrower failure-event set and sets
recovery permission for exactly the recovery-decision event. Both uncertainty-
abandonment step kinds receive neither payload permission in that factory.

## Recovery contracts

RecoveryDecisionContract fixes required scope by decision kind, requires a
nonempty typed status tuple without duplicates and validates every predicate as
an exact boolean. It rejects simultaneous uncertainty/no-uncertainty requirements
and simultaneous expired/unexpired requirements. Five claim-authority decisions
must match an exact status/unexpired/expired tuple:

| Decision | Allowed run status | Unexpired | Expired |
| --- | --- | --- | --- |
| retry-as-new-run | FAILED | true | false |
| renew-active-claim | CLAIMED | true | false |
| renew-expired-claim | FAILED | false | true |
| take-over-expired-claim | FAILED | false | true |
| abandon-expired-claim | FAILED | false | true |

Other decisions cannot require either claim-expiry state. Their status tuples
and remaining predicates are not fully fixed by kind in the constructor.
In particular, retry's no-uncertainty requirement is a canonical factory choice,
not a field checked by the claim-authority tuple comparison.

The canonical factory gives both effect-confirmation decisions PAUSED and
uncertainty; resume-same-intent gets PAUSED and no uncertainty. Retry, compensation
and acceptance of uncompensated failure get FAILED and no uncertainty, with
compensation additionally requiring compensation availability. Remaining paused
allows PAUSED, FAILED or PARTIALLY_FAILED and is the only canonical choice with
intent matching disabled. Other canonical choices retain the default requirement
to match intent. Expiry is a declared predicate: this module reads no clock,
lease, actor grant or durable intent to establish it.

## Canonical operations and constructor limits

LifecycleOperationContract requires typed kind, stage, role, optional result
status, typed status/event tuples without duplicates, exact boolean flags and
Operations as enforcement owner. Operation ID and schema names must be nonempty
ASCII letters, digits, dots, dashes or underscores; there is no length cap.
Accepted statuses and event tuples may be empty. Only ADVANCE_CURRENT_GRAPH may
set writes_current_graph true; that kind is not required by the constructor to
set it true. The constructor does not bind kind to its canonical operation ID,
stage/role pair, schema names, transitions, events or approval/worker/match flags.

The canonical factory supplies these thirteen transitions. A dash means no
accepted status or no single result status is declared, as appropriate; it does
not mean unrestricted runtime admission.

| Operation ID | Accepted statuses | Result status |
| --- | --- | --- |
| execution.admit | — | — |
| execution.claim | — | CLAIMED |
| run.start | CLAIMED | RUNNING |
| run.pause | RUNNING | PAUSED |
| run.resume | PAUSED | RUNNING |
| run.complete | RUNNING | SUCCEEDED |
| run.fail | RUNNING, PAUSED | FAILED |
| compensation.begin | FAILED | COMPENSATING |
| compensation.complete | COMPENSATING | COMPENSATED |
| compensation.fail | COMPENSATING | PARTIALLY_FAILED |
| run.cancel | CLAIMED, RUNNING, PAUSED | CANCELLED |
| recovery.decide | CLAIMED, PAUSED, FAILED, PARTIALLY_FAILED | — |
| graph.advance-current | SUCCEEDED | SUCCEEDED |

Every canonical operation requires current approval. All except admission and
compensation.begin require worker scope. Admission, compensation.begin and graph
advancement require a current-graph match; only graph advancement writes the
current graph. Admission uses ADMIT/admission; claim and cancellation use
CLAIM/lifecycle; advancement uses ADVANCE/lifecycle. Other operations use EXECUTE,
with recovery role for compensation.begin and recovery.decide, execution role
for the remaining ones. Event tuples describe their named result; claim names
both REQUEST_CLAIMED and RUN_OPENED, and advancement names CURRENT_GRAPH_ADVANCED.
These declarations do not themselves authenticate, commit, compensate or emit.

## Aggregate and descriptor admission

ExecutionLifecycleContractSet requires tuples of the appropriate record types.
For each family it checks both set equality and length against the corresponding
enum, so missing kinds and duplicate kinds cannot pass. Timing, event and
recovery records sort by enum value; operations sort by operation ID. Operation
IDs are not separately checked for uniqueness across different operation kinds.
The operation lookup returns the first matching ID, so a noncanonical set with
reused IDs is not rejected here and lookup is not guaranteed to identify one
kind uniquely. This is source-derived admission behavior, not an executed defect
or a claim that Operations constructs such a set.

The aggregate descriptor has a fixed kind and embeds request-status and failure-
category lists in enum declaration order. Decoding requires those lists exactly,
then recursively reconstructs timing, event, recovery and operation records.
Record descriptors require exact keys and typed list/text/bool fields, with
result_run_status explicitly nullable. Nested list order is retained by record
constructors; aggregate record order is normalized by sorting.

There is no raw JSON parser, canonical-byte/hash API, byte/item/depth limit or
universal public diagnostic filter. Selected ValueErrors are re-raised as
InvalidExecutionLifecycleContract with their text and cause; some checks occur
outside those wrappers. Arbitrary Mapping callback failures are not universally
translated. Exact keys and closed enum values must not be described as a complete
resource-bounded, cause-free or secret-redacting input boundary.

## Consumers, tests and operational obligations

Selected actual
[Operations records](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
derive their failure-permitting event-kind set from the canonical factory.
ActivityEventRecord uses that set and activity_event_scope to validate failure
payload permission and presence/absence of activity ID. It separately requires
typed, run-congruent recovery evidence for a recovery-decision event. This is a
concrete record-construction consumer, not proof of journal persistence or every
lifecycle service's enforcement. Only those selected record sections were read.

The full [governing test](../../../../../../control-plane-kit-core/tests/test_execution_lifecycle_contract.py)
contains thirteen tests of vocabulary, mapping reconstruction, event scope,
literal timing, selected transitions and recovery predicates, the claim-authority
matrix, selected constructor/decoder negatives and Operations/advancement
metadata. Its [companion](../../../tests/test_execution_lifecycle_contract.py.md)
records assertion-level limits. It does not cover every constructor freedom
described above or execute authorization, lease expiry, recovery, graph writes,
transactions, concurrency or provider effects.

Authoring read all 1,301 source lines, retained the full 519-line test context and
checked selected actual service enums and Operations record consumers. Security
and operational obligations remain with the enforcing services: consult current
truth, validate authority and approved intent, preserve uncertain outcomes and
record verified results. No source repair, imports, executable tests, live action
or merge occurred. Validation covers documentation links, whitespace and the
frozen source/test guard; the owner row awaits independent review.
