Source: [control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_codec.py](../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_codec.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests exercise the PostgreSQL activity-event codec for typed lease
recovery evidence and two uncertainty-abandonment event kinds. Their governing
laws are preservation across a fresh database connection, rejection at the named
SQL constraints where those constraints apply, stronger typed-record rejection
for schema-admissible invalid values, and bounded errors for the selected corrupt
recovery payloads. They exercise the actual
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py),
[current schema](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
and [records](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
They do not invoke the recovery interpreter or authenticate a caller.

The local fixture requires CPK_OPERATIONS_TEST_DATABASE_URL, connects with
autocommit enabled, installs/verifies the schema and truncates cpk_workspaces with
CASCADE during setUp. It does not inherit the recovery service fixture. Its
tearDown only closes an open connection; it does not truncate the final rows.
There is no local database-ownership guard or unique database provisioning here.
The fixed identifiers and destructive setup require the isolated test database
provided by the harness, not an arbitrary existing operations database.

seed_run constructs foreign-key scaffolding using direct SQL for the workspace,
session, plan, approval, request and run. Between those statements, the actual
[graph fixture helper](../../../../control-plane-kit-operations/tests/graph_lineage_fixture.py)
stores empty authored graphs and derived identity projections in a committed
PostgresUnitOfWork. The plan payload is raw empty JSON, not a demonstrated valid
executable ActivityPlan. Approval/request fields are synthetic fixtures rather
than evidence of admission or authorization. The seed spans autocommit statements
and that separate unit of work; it is not one atomic admission transaction.

The default seed has a CLAIMED request/run at worker-a generation seven. Decision
seeds use the replacement fence, or an ABANDONED request without claim fields.
Only the default and active-renewal cases retain a CLAIMED run; retry, expired
renewal, takeover and abandonment seed FAILED with a start time. Lease timestamps
are fixed strings. These choices support event persistence tests, not a check of
current lease authority or legal whole-journal recovery eligibility.

The first test submits five raw event shapes and requires CheckViolation naming
cpk_activity_events_shape_check: missing, null, list and scalar recovery evidence
on a recovery-decision event, plus an object recovery field on run_opened. The
schema requires an object recovery field for recovery decisions and absent/null
recovery for other kinds. This SQL check does not close the object's keys or
validate its nested fences and decision-specific relationships.

The recovery round-trip test covers RETRY_AS_NEW_RUN, active renewal, expired renewal,
takeover and abandonment. The helper constructs prior worker-a generation seven;
retry preserves that fence, renewals use worker-a generation eight, takeover uses
worker-b generation eight, and abandonment has no replacement. Each case resets
the fixture, adds its typed event, closes the connection and opens a new one.
get_event must equal the entire original record, and events_for_run must equal a
one-element tuple containing it. add_event returning the same object establishes
adapter return identity; the subsequent reads establish persistence/decoding.
Connection restart here means reconnection, not process or database-server restart.

The companion round trip stores STEP_UNCERTAINTY_ABANDONED and
STEP_COMPENSATION_UNCERTAINTY_ABANDONED with activity IDs and consecutive ordinals.
After reconnection both individual reads and the ordered run-event tuple must
equal the originals. No preceding uncertainty journal or abandonment command is
executed. A separate schema test inspects pg_constraint and requires both kind
strings to appear in the kind and shape definitions; this is substring coverage,
not complete schema equality. An invented abandonment alias must fail the named
kind check, and STEP_UNCERTAINTY_ABANDONED without activity_id must fail the named
shape check. The missing-activity negative case does not independently cover both
abandonment variants.

One raw uncertainty-abandonment event carries structurally valid failure evidence
that SQL accepts. After reconnection the ActivityEventRecord constructor must
reject it with the exact message "event kind does not permit failure evidence".
That constructor derives permitted failure kinds from the canonical lifecycle
contract. The test checks that code, message and detail canaries do not enter the
error's str/repr, that their combined length is at most 512, and that cause and
context are both absent. This protects the selected stronger record law without
claiming that SQL rejects every event-semantic contradiction.

The malformed-recovery matrix inserts 22 outer objects accepted by SQL, then
reconnects and reads each event individually. Cases cover missing/extra keys,
unknown decisions, invalid or oversized retained run IDs, missing/non-object
fences, an empty worker, boolean generation, same-worker takeover, invalid renewal
replacement worker/generation, abandonment with a replacement, changed or missing
retry replacements, and an extra nested fence key. Every selected case must raise
OperationsRecordError with no cause/context, bounded combined str/repr and no
supplied candidate canaries. The test does not assert one exact error message for
this matrix. Earlier corrupt rows remain present; it does not call events_for_run
over the corrupt collection or test repair of those rows.

The inspected decoder requires exactly four recovery keys and exactly worker_id
and generation in each non-null fence. It delegates run-ID, fence and recovery
relationship validation to their actual value constructors. _recovery_evidence
catches ValueError, exits the handler and then raises its categorical
OperationsRecordError, which avoids retaining the caught candidate exception as
context. This is distinct from raising from None inside an active handler, which
only suppresses displayed chaining. _activity_event also constructs the complete
typed event, so event-specific laws remain authoritative after nested decoding.

The internal-failure test replaces ExecutionLeaseRecoveryEvidence in the decoder
module with a constructor that raises a chosen TypeError or KeyError. Each must
escape as the very same exception object; the original constructor is restored
in finally. Those are deliberately injected implementation failures, not corrupt
payloads normalized by the ValueError path. This test does not promise redaction
for arbitrary internal exceptions or every malformed activity-event field.

Security and history evidence is limited to the represented SQL constraints,
typed event laws and selected error canaries. The file verifies stored history
values but not operation sessions/actions produced by a recovery command, approval
enforcement, provider effects, deployment restart or external resource cleanup.
Its database rows are test mutations; no credentials or live runtime are required
for this documentation review.

Read depth: all 651 source lines, including every test and local helper; selected
actual execution-store encoding/decoding methods, schema constraints, event and
recovery record contracts, fence/run-ID validation and graph fixture ownership.
This companion records source-level evidence. Documentation authoring did not
execute tests, import application modules or connect to a database.
