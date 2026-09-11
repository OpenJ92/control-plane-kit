Source: [control-plane-kit-operations/src/control_plane_kit_operations/activity_journal.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_journal.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner projects durable Operations ActivityEventRecord values into the pure
Core saga journal language. It is a small representation adapter, not a second
event store or the saga interpreter. Operations records own persisted operational
evidence; Core ActivityJournalEvent values carry the subset needed for pure
planning/state projection. The function reads supplied values, allocates new
journal events and returns a tuple, with no database, provider or runtime calls.

EVENT_KIND_TO_JOURNAL_KIND explicitly maps 17 kinds: ordinary step start/success/
failure/unsupported/uncertain; uncertainty resolved-success, resolved-failure and
abandoned; RUN_COMPENSATION_STARTED; and the corresponding eight compensation
step kinds. The target vocabulary comes from
[Core saga](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py).
The mapping is an ordinary module-level mutable dictionary, not an exhaustive
conversion of every ActivityEventKind or a frozen registry.

activity_journal_events preserves input order and copies event_id, run_id,
ordinal, mapped kind and activity_id. It drops any event whose kind is absent
from the mapping, including ordinary run-open/start/success, graph advancement
and recovery-decision records. Original ordinals are retained rather than
renumbered, so filtering can leave gaps. It does not sort, deduplicate, require
one run, validate a plan or prove a complete successful activity history.

The projection omits occurred_at, evidence, failure and recovery payloads. That
keeps provider details and authorization/recovery evidence outside the pure saga
event, but is not a universal secret scrubber: identity text is copied and needs
valid upstream construction/exposure controls. Unmapped operational history is
still important to callers; a projected journal alone cannot certify approvals,
current claim authority, run completion or compensation authorization.

The function's tuple/ActivityEventRecord annotations are not explicit runtime
type checks. It accesses fields directly and passes mapped entries to the Core
constructor, whose errors can propagate. Normal
[record construction](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
checks canonical run/activity identities, exact positive integer ordinal, typed
kind/evidence and run-versus-step activity scope. The converter does not repeat
all those checks for arbitrary duck-typed input; it also does not catch or redact
unexpected attribute/constructor failures.

Core ActivityJournalEvent performs its own local field validation; the subsequent
project_activity_journal call requires typed journal entries, unique increasing
ordinals, one run and activity membership in the supplied plan. It interprets
step/compensation/uncertainty transitions into SagaJournalProjection. That
projector accepts ordered gaps and is separate from this filter. The converter
alone neither checks those sequence laws nor produces SagaState, in-flight or
uncertain classifications.

Actual [coordinator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
context loading fetches events for the run, converts them, projects saga state
and derives a schedule. The
[effect-start service](effect_attempt_start_interpreter.py.md) uses the same
projection in readiness checks, with its own request/run/plan authority gates.
[Current advancement](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
also checks unfiltered run/failure history and exact success coverage in addition
to projection/schedule success. Filtering away run events is not permission to
ignore those separate completion checks.

Selected [lease-recovery support](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_execution_lease_recovery_support.py)
first checks retained events' run/contiguous ordinal shape and removes recovery
pairs before projection. It also uses membership in this mapping to distinguish
lifecycle events from saga events. Changing the mapping can therefore change
recovery interpretation as well as scheduling; a new durable event kind should
be deliberately included or excluded with those consumers in view. Their full
recovery/lifecycle semantics are not owned or reviewed wholesale by this note.

The assigned [activity-identity tests](../../tests/test_activity_identity.py.md)
do not call this converter. They protect neighboring record/consumer identity
contracts and the Core/Operations import boundary, not all 17 event mappings.
Selected actual tests elsewhere directly cover mapping both uncertainty-abandonment
kinds in [run lifecycle](../../../../../control-plane-kit-operations/tests/test_run_lifecycle.py),
and preservation of a 200-character run ID in
[authoritative run identity](../../../../../control-plane-kit-operations/tests/test_authoritative_run_identity.py).
Only those selected assertions were inspected for this group; they are not a full
review of either suite or exhaustive filtering/order/error coverage.

Read depth: full 77-line owner and 289-line assigned identity test; selected actual
Core journal constructor/projector and ActivityId grammar, Operations record and
coordinator/start/advancement/recovery consumers, plus the two focused neighboring
test excerpts. Existing rotation/kernel context was retained. No executable tests,
source changes, credentials, database/provider/runtime action or publication was
performed for this documentation. It adds no security or mutation surface.
