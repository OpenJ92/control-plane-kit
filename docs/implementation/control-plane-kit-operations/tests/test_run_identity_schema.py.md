Source: [control-plane-kit-operations/tests/test_run_identity_schema.py](../../../../control-plane-kit-operations/tests/test_run_identity_schema.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This file has three static schema-contract tests and five PostgreSQL tests. They
protect six direct run-ID checks, two foreign-key-derived identity positions and
the installer's refusal to repair schema drift implicitly. PostgreSQL cases use
real SQL and catalog observations when executed; this documentation review did
not run them. The file does not exercise run-record codecs, service authorization
or a deployed runtime.

The static contract test pins 40 relations, 507 columns, 382 constraints and 131
indexes in CURRENT_POSTGRES_SCHEMA_CONTRACT. These are exact baseline inventory
counts, not live catalog counts independently calculated by that test. The
selected entries in the actual
[contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
and [DDL](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
define the same named direct checks:

- cpk_activity_runs_run_id_check for activity_runs.run_id.
- cpk_cloudflare_ingress_resources_source_run_id_check for ingress source_run_id.
- cpk_cloudflare_ingress_resources_removed_by_run_id_check for nullable removal provenance.
- cpk_gateway_key_rotation_deployments_run_id_check for deployment run_id.
- cpk_generated_ingress_secret_references_source_run_id_check for generated-secret source_run_id.
- cpk_secret_use_authorizations_run_check for nullable secret-use run_id.

Each expression applies the regex ^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$ with C
collation; the two nullable cases explicitly allow NULL. Static assertions check
the expected relation, check kind, validated/nondeferrable/nondeferred state,
single local column and exact expression. The first live test compares a selected
catalog mapping of all six names to relation, validated flag, deferrable flag and
pg_get_expr output. That live query does not separately observe condeferred.

The core [run-ID predicate](../../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py)
requires exact Python str and a corresponding ASCII grammar with a strict end
anchor. SQL operates on stored text and cannot establish the Python exact-type
law. These SQL tests cover thirteen invalid strings, including empty/space-only
text, four forbidden leading punctuation characters, slash, internal space,
embedded newline, trailing newline/carriage return, non-ASCII text and length
201. Every candidate is applied to every direct column and must raise
CheckViolation naming that column's exact expected constraint. This is a selected
cross-layer language check, not an exhaustive equivalence proof over all text.

The live boundary test updates each selected row to a and then 200 a characters,
reads a matching column value back through SQL and restores its original value.
_restore_direct_value finds either boundary value and replaces matching column
values with the fixture's original. There is no rowcount assertion or typed-store
decode; the positive read is a matching-value SELECT with LIMIT 1. Separate reads
confirm NULL removal provenance on an active ingress and NULL run provenance on
one secret-use authorization. No connection is closed and reopened for these
round trips.

The two derived positions are activity_events.run_id and activity_runs.prior_run_id,
with named FKs cpk_activity_events_run_id_fkey and
cpk_activity_runs_prior_run_id_fkey targeting activity_runs.run_id. Static checks
assert relation/column endpoints and validated, nondeferrable, nondeferred FK
metadata. Both static and live checks reject a duplicate check whose local-column
tuple is exactly the corresponding singleton tuple; this does not rule out every
multi-column check mentioning that column. The live FK metadata query checks kind
and flags, while endpoint comparison is supplied by the static contract test.

The FK behavior test inserts an event referring to run-root, rejects an event
referring to run/bad with the event FK name, rejects the same invalid predecessor
with the prior-run FK name, then inserts a retry row referring to run-root. An
attempt-three row with its own run/bad identity must instead fail the direct run
check. FK rejection here establishes failure to reference a valid stored parent;
it is not an independent regex on the child field. Canonical-looking missing
parents and cross-request retry legality are not separately exercised.

Each PostgreSQL case requires CPK_OPERATIONS_TEST_DATABASE_URL, opens one
autocommit connection, creates a UUID-named run_identity schema, sets search_path
to it, installs the current schema and seeds raw SQL rows. tearDown restores
public search_path, drops that schema with CASCADE and closes the connection.
The drift helper drops/recreates the same owned schema between variants. The
unique schema isolates object names, but the file neither provisions the database
nor guards its ownership; it relies on the disposable test environment. Setup
failure before unittest invokes tearDown, or an exception during cleanup, can
leave cleanup incomplete because these helpers have no encompassing finally.

The seed uses one multi-statement SQL execute to construct workspace, authored
graph and fixed identity projection, open session, raw empty-plan payload,
approval, claimed execution request, succeeded run, ingress resources, rotation
checkpoint, generated-secret reference and secret provider/reference/use rows.
The claim/start/settlement times are ordered fixture data. Provenance strings
such as run-source and run-secret need not be rows in activity_runs: their direct
checks establish syntax, unlike the two derived FKs. Secret references and
Cloudflare identifiers are synthetic; no service admits a plan, grants secret
use, generates a token or creates a tunnel here. The raw plan payload does not
demonstrate successful ActivityPlan decoding.

Exact installer reentry first checks the seeded request/run status, worker and
three timestamp-order comparisons. It snapshots schema pg_class/constraint names
and OIDs plus run_id, prior_run_id, attempt and status for activity runs, then calls
install_schema through a recording delegate on the same connection. Those object
identities and selected run columns must remain unchanged. This is repeated
installation/verification, not process restart, reconnection or a full-table data
snapshot. No preservation assertion compares every field of every seeded row.

The delegate exposes autocommit/transaction and records execute query text before
forwarding calls. Both reentry and drift tests reject recorded SQL containing the
words create, alter, drop, truncate, insert, update or delete after whitespace
normalization. This is a syntactic guard on these execute calls, not a complete
effect monitor. It permits advisory/table locks and does not capture transaction
control performed by the delegate's context manager.

Drift cases start from a newly installed schema with a sentinel workspace and
make the activity-run check missing, weakened to nonempty, renamed, replaced with
a byte-length expression, NOT VALID, or accompanied by an extra check. Every
installer attempt must raise the exact SchemaInstallationError type and message
"operations schema reset is required", with no cause/context. Constraint
name/OID/validation/expression snapshots, relation/constraint OIDs and the sentinel
row must remain unchanged. The test applies the drift itself before recording;
it proves selected installer rejection without repair, not a prohibition on the
fixture's intentional DDL or a successful reset/migration workflow.

The fully read [installer](../src/control_plane_kit_operations/postgres/schema.py.md)
uses a transaction and namespace-scoped advisory lock. An object-free namespace
receives the current DDL; an existing namespace must have expected relations,
then undergo SHARE relation locking, contract/adjunct verification and selected
current-row validation. A mismatch becomes the bounded reset-required error
after the exception handler exits. The inspected
[row-validation entry](../src/control_plane_kit_operations/postgres/current_data_validation.py.md)
checks referenced graphs/projections and delegates selected durable-record
validation; it is not a general decode of every seeded plan/run/provenance row.
This file's drift matrix is limited to one run constraint, not all schema objects.

Security and data evidence is database-side canonical text admission, FK lineage
and refusal to mutate selected existing truth during verification. Direct
PostgreSQL errors are inspected for constraint identity, not secret redaction;
only the reset-required error has chain-free categorical assertions. Live
credentials, authorization, provider cleanup, concurrent installation, interrupted
installation and deployed restart/history remain outside this test file.

Read depth: all 677 test/helper lines; full installer, selected actual frozen
contract/DDL entries, catalog-verification entry points, row-validation entry and
graph scan, and retained full core run-ID predicate review. No tests or executable
imports were run and no database or provider was accessed while writing this note.
