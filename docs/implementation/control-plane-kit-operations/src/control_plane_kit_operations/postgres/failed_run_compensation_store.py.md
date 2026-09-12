Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 323-line PostgreSQL adapter stores and reconstructs one admitted failed-run
compensation program and projects source successes for admission. Its sole export
is FailedRunCompensationStore. The constructor retains a connection supporting
execute; it does not open, commit, roll back or close that connection. The store
bundle supplies the caller's transaction connection. There are no provider calls,
execution dispatch, authorization decisions, retry loops or cleanup methods here.

successful_effects_for_run selects seven outcome columns: run/activity/attempt,
request/outcome fingerprints and direct completion event ID/ordinal. It joins the
attempt on run/activity/attempt and the event on the outcome's direct-event ID,
run and ordinal. Both attempt and outcome must be succeeded, their request/outcome
fingerprints must match, and the event type must be step_succeeded. Results are
ordered by completion ordinal descending, then activity ID ascending. The query
does not select only the latest attempt per activity or require a failed run.

This projection does not decode outcome preimages, source intent, endpoint
observations or event evidence, nor compare the attempt's latest-event columns to
the projected completion. It does not recheck workspace, approval, lease or plan
meaning. Those responsibilities are split between stored schema/record invariants
and command owners. A projected success is selected relational evidence, not full
provider-outcome authentication or proof of every source record's congruence.

_successful_effect wraps each row in Core EffectAttemptIdentity/RunId and
SuccessfulEffectEvidence. Their constructors enforce identity, fingerprint and
positive completion-coordinate laws. TypeError or ValueError becomes a chained
OperationsRecordError. The helper does not explicitly validate row type or length;
IndexError and unrelated failures are not covered by that catch. SQL uses the
passed run ID as a parameter without local identifier admission.

unresolved_attempt_count counts STARTED and UNCERTAIN attempt rows for a run;
succeeded_attempt_count counts SUCCEEDED rows. They convert the first count column
with int and do not add source-outcome consistency, missing-row normalization or
row-shape guards. These and the projection are separate queries without their own
locks or enclosing transaction. The
[admission owner](../failed_run_compensation.py.md)
uses them under its request/run/workspace transaction locks, rejects nonzero
unresolved count and requires success count to equal projected evidence count.
The adapter does not enforce that admission policy itself.

insert requires exact FailedRunCompensationRecord and FailedRunCompensationProgram
outer types. It does not reconstruct those values or cross-check record against
program before writing. It serializes program.descriptor using sorted compact
JSON, ensure_ascii=True and ASCII bytes. This is deterministic encoding of the
program descriptor, not an independent generic canonical-JSON implementation.
The record's supplied fingerprint strings are stored as supplied; insert does
not recalculate or compare them with the serialized program.

The parent insert writes seventeen columns: program/workspace/request/run/plan/
session/action/event/actor coordinates, reason, source failure, four fingerprints,
program preimage and creation time. Source failure is converted to category/code/
message/details and adapted as JSONB; program preimage is bytea. Creation time
passes through the actual
[timestamp codec](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py),
which accepts canonical UTC text and produces an aware datetime. Readback converts
aware datetimes to canonical UTC text. The shared text validator also checks actual
calendar validity and canonical seconds/microseconds rendering.

For every program step, a separate insert writes eleven columns: program/position,
source attempt identity, request/outcome fingerprints, completion event/ordinal,
operation JSONB and material-source text. The parent ID comes from record.program_id,
while step parent IDs come from program.program_id. Coherent callers supply matching
values; this method does not make mismatched exact-type inputs coherent. It returns
None, ignores cursors/affected counts, has no ON CONFLICT replay path and performs
no post-write readback. Duplicate or constraint errors propagate from PostgreSQL.

The selected
[current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
requires a parent program key and unique run/action/event coordinates; steps have
program/position and source-identity uniqueness constraints. Foreign keys connect
the parent to workspace/request/run/plan/session/action/event truth and steps to
their parent, source outcome and completion event. Shape checks cover reason,
identifiers, fingerprints, positive positions/completion and material source. The
parent preimage is bounded to one through 1,048,576 bytes by the schema. This method
does not perform a separate size check before building or writing those bytes.

Database constraints do not by themselves prove every parent fingerprint matches
its preimage or that every operation is the intended inverse. The store's readback
and the command owners add those semantic checks. insert's grouped writes are
atomic only when the supplied connection participates in a correctly managed
transaction; passing an autocommit connection would change that behavior. The
actual caller uses
[PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
to roll back partial writes and commit the whole admission command.

get and get_for_update share _get. Both read the seventeen parent columns, then
ten step columns ordered by position. Only get_for_update appends FOR UPDATE to
the parent SELECT; the step SELECT has no explicit FOR UPDATE. There is no advisory
lock or lock acquisition for an absent parent. Parent-lock cooperation supplies
the service's serialization boundary; these two reads alone do not promise a
single immutable snapshot against arbitrary concurrent direct SQL changes.

A missing parent raises KeyError with a fixed message before step lookup. Otherwise,
_get converts the preimage through bytes(...).decode("ascii"), parses JSON, and
delegates to
[Core program reconstruction](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py).
That owner admits the program/evidence/lineage/step descriptors and enforces nonempty
unique reverse-completion evidence and contiguous exact step coverage. This store
does not compare the stored preimage bytes to a canonical re-encoding, install a
duplicate-key rejection hook in json.loads or impose its own pre-parse byte bound.
Canonical writer output and semantic readback congruence are different guarantees.

The record is reconstructed from parent columns, including decoded source failure
and timestamp. The record constructor supplies text/fingerprint/type checks.
_failure requires an exact dict with category, code, message and details, then
constructs FailureCategory, BoundedEvidence and FailureEvidence. It does not apply
the admission command's narrower four-key source-failure detail allowlist here.
The imported evidence constructor's bounds and key rules are not a universal
secret-value redactor; failure message/details are persisted and returned.

TypeError and ValueError during parent program/record reconstruction become a
chained OperationsRecordError for an invalid row; OperationsRecordError itself is
a ValueError subclass. Parent/step reads are outside this catch, and neither parent
nor step row has an explicit fixed-length/type check. Short-row indexing and other
unexpected dependency failures are not universally normalized. Errors retain
causes in the wrapped paths, so a fixed outer message is not proof of complete
exception-chain redaction.

After reconstruction, _get compares record/program ID, workspace/request/run/plan
lineage and reason. It hashes the full reconstructed source failure and compares
that hash with evidence's source-failure fingerprint, recomputes the evidence hash,
and compares the program fingerprint with program.fingerprint(). _fingerprint uses
the same sorted compact ASCII JSON shape as the writer. Finally, the ordered
relational step descriptors must equal the program's step descriptors exactly.

_step_descriptor maps the ten selected columns into position, source-effect
identity/fingerprints/completion, operation and material-source shape. It does
not independently reconstruct each relational step's operation or typed identity;
it compares this dictionary with the already reconstructed program step. Deleted,
reordered or semantically altered relational steps therefore cause an incongruent
row error. The descriptor construction and comparison are outside the reconstruction
try block; arbitrary malformed rows are not all converted to the same error.

The store does not recompute authority-reference or command fingerprints from
private material, load the referenced action/event/run to validate their payloads
and states, or recheck current successful effects during program readback. The
record only checks those fingerprint shapes. The admission command's replay binds
record to operator command/action/event/run, and the subsequent
[attempt owner](../failed_run_compensation_attempt.py.md)
revalidates current lease/approval plus full selected source attempt/outcome/intent
before binding an inverse. Returning a pair here is representation validation,
not sufficient authorization to execute compensation.

The reviewed
[base fixture](../../../tests/failed_run_compensation_fixture.py.md)
constructs actual typed synthetic successes and uses these projection/insert paths
through admission. The reviewed
[PostgreSQL admission suite](../../../tests/test_postgres_failed_run_compensation.py.md)
checks two reverse-order steps, one parent, persisted replay, altered parent/step
truth and rollback after parent/step writes. It also checks same/competing-key
callers through the service. Those are service-level assertions with selected
snapshots, not an exhaustive isolated store test matrix. No claim is made here
that every fake-row shape, noncanonical preimage, size boundary, writer mismatch
or direct-SQL race has an independent test in that suite.

The adapter preserves program preimage plus relational coordinates for downstream
history and binding. It does not emit separate events or logs; the caller writes
the admission event/action and run transition. It stores provenance fingerprints
rather than raw operator authority references, but returns full failure/program
data and supplies no public redacted projection. No network endpoint or provider
mutation is introduced by this adapter.

Read depth: the complete 323-line adapter and every helper were refreshed, with
retained full Core compensation-value, admission529, attempt488, fixture459 and
PostgreSQL suite569 context. Full timestamp codec/shared UTC validator and selected
table/constraint/index definitions and store-bundle wiring were checked; full
unit-of-work context was retained. No full schema, records, store bundle or
dependency review is claimed. Validation was documentation-only: local links,
whitespace and frozen-source comparison. No application imports, tests, database/
provider calls, credential access, source/inventory edits or publication were
performed.
