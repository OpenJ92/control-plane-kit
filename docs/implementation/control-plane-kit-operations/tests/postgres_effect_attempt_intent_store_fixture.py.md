Source: [control-plane-kit-operations/tests/postgres_effect_attempt_intent_store_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_intent_store_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 158-line fixture extends PostgreSQL effect-attempt start setup with helpers
for matching attempt/intent evidence, ordered persistence and raw intent-table
snapshots. It has no test methods of its own. Its consumers mix it with unittest;
the assertions and database calls in these helpers are apparatus for those tests,
not independent execution evidence or an effect-start service implementation.

The optional module loader suppresses only ModuleNotFoundError whose name equals
the requested private store module; nested dependency failures escape. Store and
current-row-validator names are captured once at module import. require_intent_store
checks only that the captured store class is non-None, not that every optional
name is present or that the active store bundle has been inspected.
require_intent_schema checks membership of cpk_effect_attempt_intents in the
frozen CURRENT_POSTGRES_SCHEMA_CONTRACT returned by a local import. It does not
query the live catalog or validate all columns/constraints. The actual store is
[bundle-owned](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
and operates on the caller's connection.

The inherited [start fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_start_fixture.py)
delegates setup through the
[attempt-store fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_store_fixture.py)
to the [recovery fixture](execution_lease_recovery_fixture.py.md). That base requires
CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit connection, installs/verifies
the schema and truncates cpk_workspaces with CASCADE. Inherited reset helpers seed
constructed workspace/approval/request/run truth; the start fixture directly
updates the run to RUNNING or COMPENSATING and adds a RUN_STARTED event for the
ordinary path. This is fixture preparation, not execution through the start
command service. It depends on an isolated test database rather than a unique
per-test schema created by this file.

Inherited teardown truncates and closes only when the setup connection is open;
there is no finally guaranteeing close if truncation fails. A separate connection
is created for each unit of work. The scope of these resets and their synthetic
lease/approval data belongs to the base fixture, not a provider cleanup or live
authority guarantee. This file adds no database lifecycle override.

intent_attempt selects a supplied intent or calls the inherited intent builder.
That builder adapts the earlier [pure intent fixture](effect_attempt_intent_fixture.py.md)
to graph-current/graph-desired and StartRuntime(runtime-a), or StopRuntime for
compensation, with process delivery disabled by default. Thus this fixture's
default intent is runtime-level, not the pure fixture's StartNode(api) request.
Products can remain represented material without an executed image pull or
runtime operation.

The helper then calls inherited record("started") with the selected run/activity,
compensation flag, event ordinal and fixed 2030 timestamp. This call is not wholly
pure: the inherited record's request-fingerprint construction reaches the start
fixture's intent_for_attempt, which SELECTs request_id and plan_id from
cpk_activity_runs for the requested run. The run must already be seeded. The
query does not lock it or authenticate the caller, and this occurs even when a
custom intent is supplied to intent_attempt.

After that initial record, dataclasses.replace rebuilds its state with the actual
runtime_effect_intent_fingerprint of the chosen intent. The original event is
rebuilt with the requested event ID/ordinal and fresh evidence_for(state). The
inherited [record fixture](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
computes that evidence from sorted compact UTF-8 JSON of the state descriptor
with ensure_ascii=False and SHA-256. It is a computed state commitment here,
unlike the pure intent fixture's all-a placeholder event fingerprint.

Finally the helper constructs EffectAttemptRecord(state, event, event) and
EffectAttemptIntentRecord(state.identity, event, intent), returning that pair.
The actual [attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
requires a STARTED record's latest event to equal its original and verifies the
event's state commitment. The
[intent owner](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
binds the run/activity and canonical intent to the start event. These are typed
value checks. A supplied intent with incompatible coordinates can fail; the
helper does not silently rewrite every caller input or authorize the resulting
attempt against current lease truth.

indexed_intent_attempt derives activity/event IDs using a minimum three-digit
index format and sets ordinal to 3 + index. It delegates the same construction;
it does not independently validate the index, allocate a sequence from the store
or make repeated calls with the same index unique. Consumers must choose indexes
that fit their seeded run and existing event ordinals.

persist_evidence_chain opens one unit of work and writes, in order, the original
start event, intent evidence and attempt record. Each adapter return must equal
its corresponding supplied value, then commit is requested. These are equality
checks, not object-identity or subsequent read-back assertions. The actual
[intent store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
reconstructs admitted evidence and stores its canonical preimage. The attempt
store's insert_absent returns None on an identity conflict; this helper therefore
expects a fresh chain, rather than treating a duplicate as successful replay.
It does not verify caller-pair coherence separately before the adapter/constraint
boundaries or retry partial failures itself.

The actual [unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
gives these stores one transaction connection. commit marks intent to commit;
successful context exit performs the commit, while failure or no commit request
rolls back and the connection is closed. A commit exception triggers a rollback
attempt. This explains the intended transaction boundary, but this helper alone
does not inject failures or prove recovery after an ambiguous commit outcome.

intent_snapshot checks static relation membership then selects ten columns from
the entire intent table, ordered by run, activity and attempt. It includes raw
preimage bytes, provenance, request fingerprint and original-event coordinates.
It has no workspace filter, row/page limit, preimage transport guard or joined
event/attempt validation. It is a test snapshot, not the store's bounded decoder
or a redacted public history endpoint; callers should not treat it as either.

largest_lawful_intent explicitly reuses the pure fixture's binary-search builder
with self, so inherited intent overrides still apply. It returns a large admitted
prefix from the fixed family under the one-mebibyte canonical-byte ceiling, not
an exactly maximal payload. The selected
[PostgreSQL consumer](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store.py)
persists that chain, opens a fresh connection, loads the record and compares the
whole record, intent and request fingerprint. That is real reconnection/codec
evidence when executed, not a process or database-server restart.

Another selected consumer inserts event/intent in a unit of work without requesting
commit, then successfully persists the same chain through this helper. Its shape
relies on rollback of the first scope; it does not simulate failure after an
uncertain commit acknowledgement. A separate
[contract consumer](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store_contract.py)
checks the private insert/get surface and bundle connection with a recording
connection. These crossreads establish where assertions live, without claiming
full review of either consumer.

Security and operational limits follow those boundaries: constructors represent
intent and state commitments, stores persist evidence in a caller transaction,
and synthetic setup supplies the required lineage. No credentials are resolved,
no runtime effect is executed, and no authentication or fresh-lease decision is
made by this fixture. Raw snapshots contain protected intent material and are not
proof of secret redaction. Resource cleanup beyond the test database belongs to
other owners.

Read depth: all 158 fixture lines/helpers; actual store implementation and unit
of work, selected bundle/attempt insert and record commitment paths, inherited
setup/intent/record/fingerprint helpers and selected consumer assertions, with
the preceding full intent fixture/evidence-owner review retained. No tests,
application imports, database connections or provider actions ran during authoring.
