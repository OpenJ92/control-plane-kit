Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_schema.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_schema.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests protect the effect-intent relation's contract, ownership and
uniqueness constraints, selected wide-value storage, current-row completeness and
installer/documentation integration. All belong to a class inheriting the
[PostgreSQL intent-store fixture](postgres_effect_attempt_intent_store_fixture.py.md),
so even methods that only inspect Python contract values or repository text use
its database setup when run normally. That setup constructs approval/request/run
truth and truncates test workspace data; this file does not create an isolated
schema of its own or execute an effect-start service.

The first test pins the current contract at 40 relations, 507 columns, 382
constraints, 131 indexes and 87 FKs. The intent relation must occur exactly once
and expose exactly ten columns in order: run_id, activity_id, attempt, workspace_id,
request_id, request_fingerprint, original_event_id, original_event_run_id,
original_event_ordinal and preimage. These are assertions about the frozen
[contract value](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py),
not an independent live-catalog enumeration or a comparison with a previous schema
showing how many relations were added.

Three keys establish distinct identities. cpk_effect_attempt_intents_pkey owns
(run, activity, attempt); cpk_effect_attempt_intents_original_event_key owns the
original event's (ID, run, ordinal); cpk_effect_attempt_intents_commitment_key
contains attempt identity, request fingerprint and original event ID. The static
test accepts either primary or unique kind for each named key and checks relation
and ordered columns. The actual
[DDL](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
uses the first as PRIMARY KEY and the other two as UNIQUE.

Four named FKs connect the values: run/request to activity_runs, request/workspace
to execution_requests, the original-event triple to activity_events, and an
attempt's five-field commitment to intent evidence. The static assertions check
their exact endpoint tuples, NO ACTION update/delete codes and nondeferrable,
nondeferred flags. They do not separately assert validated flags or execute
deletion/cascade scenarios. The reduced inbound commitment leaves ownership and
event-ordinal coordinates to the other relationships instead of copying every
column into one wide key.

The check-contract test requires four named checks and selected expression
fragments: positive attempt with C-collated run/activity IDs, equality of original
event run and intent run, lowercase 64-hex fingerprint and a preimage byte length
between one and 1,048,576. It does not compare complete expressions or exclude
all extra checks. The actual DDL uses canonical ASCII run/activity regexes and
those byte bounds, but this method supplies no independent malformed-byte codec
or complete identity-boundary matrix.

The first live mutation test inserts and commits a start event and intent row,
then requires one row in the raw snapshot. Despite the lawful-chain test name,
this setup does not insert the attempt record. Three SQL updates must fail with
the exact run/request, request/workspace or original-event FK name. Changing
original_event_run_id must instead fail the named ownership check. This proves
the selected SQL ownership relationships, not service authorization, preimage
semantic equality or completeness of an event/intent/attempt chain.

The simultaneous-width test constructs a 512-character request ID, 200-character
run and activity IDs, and an original event ID containing 512 copies of U+10FFFF.
It copies the existing plan, request and run rows into new fixture identities,
rebuilds the intent source and persists the full event/intent/attempt chain.
A SQL query must report run/activity lengths 200, event length 512 characters
and 2,048 octets. The successful inserts exercise the actual indexes with these
combined widths. They do not measure an index-page budget, prove every possible
wide value is admissible or establish an exact overall payload maximum.

The copied request retains approval references from the original fixture while
using the new plan. This is SQL scaffold, not a newly approved deployment plan or
an authorization-service result. Inherited intent_attempt also performs its run
request/plan lookup before constructing matching evidence. The test checks stored
widths, not a fresh-connection typed read or a complete current-row validation of
every copied approval/plan relationship.

The missing-evidence test first inserts an event, then requires attempt insertion
to fail with cpk_effect_attempts_intent_evidence_fk. That unit of work requests no
commit and is rolled back. A second scope commits event plus intent without an
attempt, and validate_current_rows must raise CurrentRowDrift. These checks cover
opposite directions: SQL forbids an attempt without its intent; semantic current
validation rejects orphan intent evidence that the FK direction permits.

The actual [intent scanner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
decodes rows in bounded pages and then checks for a matching attempt by identity,
request fingerprint and original event ID. The
[current-row entry](../src/control_plane_kit_operations/postgres/current_data_validation.py.md)
invokes that scanner and translates selected validation errors into CurrentRowDrift.
The test asserts that error class, without a message/canary/exception-chain check
or a spy proving which internal validation function raised. The inspected source
explains the intended orphan path.

The uniqueness test persists a complete chain and a separate valid event. Raw
INSERT SELECT with the same attempt identity but changed event coordinates must
fail the primary key. A second INSERT SELECT changes attempt number while reusing
the original event coordinates and must fail the independent event key. These
are named uniqueness assertions; the copied preimage in the second candidate
does not establish a lawful next attempt, and neither candidate is reconstructed
through the typed record before SQL. There is no concurrency race or idempotent
service-replay test here.

The installer test opens a fresh connection to the already prepared database and
calls install_schema, reads the intent row count, drops only the ownership check
within that connection's transaction and calls installation again. It requires
SchemaInstallationError with the exact reset-required message and an unchanged
row count. It inserts no intent chain in this method. This is existing-schema
verification and selected drift rejection, not an isolated fresh-schema creation
test or evidence of a process restart.

finally rolls back the constraint change and closes the connection; rollback and
close are sequential, so a rollback exception can prevent close. The test does
not compare object OIDs, constraint snapshots, all row values or recorded SQL
mutations, and does not assert error cause/context. The actual
[installer](../src/control_plane_kit_operations/postgres/schema.py.md) uses a transaction,
namespace advisory lock, SHARE relation locks and exact contract/current-row
verification; it reports existing drift instead of applying a migration. That
implementation context is stronger than this test's row-count preservation check.

The final method reads the package
[table atlas](../../../../control-plane-kit-operations/OPERATIONS_TABLE_ATLAS.md)
and checks the intent heading, a fixed schema-digest marker, foreign-key count,
all expected key/FK names and textual restore/teardown sequences. Restore places
events before intents before attempts; teardown reverses that order. These are
documentation substring assertions, not execution of backup, restore or deletion.
The test does not recompute the schema digest or validate every atlas statement.

It also reads current_data_validation.py as text, requiring one occurrence of the
store-module name and two of the imported validator alias. This is lexical wiring
coverage, not AST control-flow or a count of runtime calls. Finally it parses
[read-cardinality metadata](../../../../control-plane-kit-operations/POSTGRES_READ_CARDINALITY.toml)
and requires exactly one entry for the store with selector _validate_current_rows,
no occurrence value, and SQL-description text containing 8-row and keyset. It
does not execute a cardinality audit, inspect bound page limits or validate the
metadata's remaining fields.

Security/data evidence is relational ownership, immutable-evidence commitments,
selected completeness checks and schema-drift rejection. Direct SQL diagnostics
are checked for named constraints, not redaction. Synthetic credentials/references
are never resolved; run/plan IDs do not themselves grant authorization. Teardown,
restoration and rollback concern the test database only; there is no provider
mutation, runtime compensation, deployed restart/history check or live cleanup.

Read depth: all 460 source lines and nine tests; actual named contract/DDL
entries, installer and current-row wiring, intent orphan check, atlas section and
cardinality entry, with preceding full fixture/store/evidence reviews retained.
This companion records source assertions and their limits. No tests, application
imports, database connections, source changes or provider actions ran in authoring.
