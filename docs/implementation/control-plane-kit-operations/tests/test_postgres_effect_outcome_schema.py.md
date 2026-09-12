Source: [control-plane-kit-operations/tests/test_postgres_effect_outcome_schema.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_schema.py).
Maintain this document alongside its source. Recheck the actual SQL, current contract,
installer/row verifier and fixture rather than treating asserted metadata as the
whole implementation.

This 511-line suite has seven tests for the two direct-outcome relations and their
current-schema admission. It mixes in-memory contract assertions, real PostgreSQL
constraint failures, traced current-row scanning and selected documentation checks.
All methods inherit the
[PostgreSQL outcome fixture](postgres_effect_outcome_store_fixture.py.md), including
methods that only inspect metadata or files. Setup therefore requires the owning
Docker suite's database, schema and destructive workspace reset. It seeds an active
leased run; teardown resets through the inherited fixture. No executable validation
or SQL was performed while writing this note.

OUTCOME and MEMBERSHIP are fixed relation names used in test SQL interpolation.
The expected column tuples have 22 and seven names. The first test requires both
relations in a 40-relation contract, exact ordered column names for each, and global
totals of 507 columns, 382 constraints and 131 indexes. It does not compare every
property of all those objects or prove that this test owns every later schema change.
The total counts are current metadata assertions, not runtime cardinalities.

The second test selects eight candidate keys and seven composite foreign keys from
[CURRENT_POSTGRES_SCHEMA_CONTRACT](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
Candidate keys cover run/request, request/workspace, observation/workspace, outcome
attempt identity, direct event, outcome membership identity/count, member position
and relation-wide observation identity. Each selected key must have kind p or u,
the expected relation and exact local-column order. It does not distinguish which
of p or u each name must be, nor test every index or constraint attribute there.

The seven foreign-key expectations bind outcome to attempt, run/request,
request/workspace and original/direct event triples; membership binds to the
outcome's identity/workspace/count and observation/workspace. Each must have kind f,
exact local/referenced relation-column tuples and update_action=delete_action=a.
That PostgreSQL code means NO ACTION, not CASCADE and not the distinct RESTRICT
code. The test name's restrictive wording means references prevent invalid changes;
it does not establish identical timing to RESTRICT for deferred constraints.

Ten check constraints are inspected through selected substrings: identity grammar,
positive fence generation/worker length, two profiles, 1..8192 preimage octets,
fingerprints, immediate-prior shape, direct status, event progression, observation
count and member position. These checks are not full expression equality or SQL
parsing. The inspected actual
[current_schema.sql](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
and selected contract entries agree on the stated shapes, with typed NOT NULL columns
except the optional prior triple. Outcome status excludes started and abandoned.
The schema checks neither decode the inner preimage nor establish provider truth.

Actual SQL makes observation identity unique with a deferrable, initially immediate
constraint, and membership-to-outcome identity/count a deferrable, initially deferred
foreign key. The selected metadata loop does not assert these deferral properties;
the source and transactional position case provide additional context. The outcome
prior-shape check is not itself a foreign key to an earlier outcome row. Historical
outcome state need not equal a current attempt changed later by recovery.

The raw-check test persists one synthetic execution-success outcome, then attempts
five updates through the setup connection: empty preimage, 8193-byte preimage,
observation_count=8193, status=started and position=observation_count. Each must
raise CheckViolation naming the exact intended constraint. These are negative
examples, not every scalar boundary. Autocommit means each failed statement rolls
back independently; this loop does not intentionally retain those malformed rows.

The composite-ownership test resets and persists for four separate mutations of
request ID, workspace ID and original/direct event ID to foreign values. Each must
raise the named corresponding ForeignKeyViolation. A fifth case changes the copied
membership count to three without changing its parent and requires the outcome
foreign-key name. It does not create complete alternative foreign tenant records,
exercise every composite column or test authenticated caller access.

The one-member test opens an explicit transaction, deletes the second member, changes
the parent and remaining member counts to one and tries to move the remaining
position from zero to one. It requires the position CheckViolation. The deferred
membership count relationship permits the intermediate parent/member updates;
the exception rolls back the transaction. This establishes the selected single-member
position boundary, not full membership completeness from SQL constraints alone.

The late-drift test creates 129 indexed synthetic outcomes in one committed unit
of work, including real event/intent/attempt/observation prerequisites. Index 64 is
execution-succeeded with two observations; the other 128 are observed-absent with
none. _TracingConnection records execute(query, parameters) while delegating to the
real connection and forwards other attributes. It is not a fake SQL result source.

A valid install_schema call must produce exactly three selected outcome scans.
The helper filters statements containing the outcome FROM clause and ORDER BY,
then requires primary-key ordering by run/activity/attempt, LIMIT placeholder and
last parameter 64. The first scan must have no tuple-seek predicate, later scans
must have a strict tuple greater-than seek and four parameters. These assertions
do not compare each returned page or the exact continuation values.

The valid trace must also contain 129 membership queries, each with LIMIT and no
FOR UPDATE. Their limits must be 128 occurrences of one and one occurrence of three,
matching observation_count+1. This is a finite real scan witness for the seeded
distribution, not a constant query-count guarantee for arbitrary data or a bound
on total database work, elapsed time or bytes.

The last outcome's preimage is then changed through autocommit to a small invalid
JSON object. The test reads before/after bytes to establish an actual change.
install_schema must raise the fixed reset-required SchemaInstallationError, omit
the late-drift canary, still traverse the three selected pages and leave that row's
preimage equal to the corrupt bytes afterward. This proves no repair of the inspected
row. It does not compare every table or statement for absence of all mutation.

The actual [installer](../src/control_plane_kit_operations/postgres/schema.py.md)
opens a transaction and obtains a namespace-scoped transaction advisory lock. For an
existing namespace it verifies required relations, takes SHARE locks on current
relations, checks exact schema/adjuncts and validates current rows. Consequently,
no FOR UPDATE in the selected page SQL does not mean the complete installer is
lock-free. The installer translates recognized drift to reset-required; it does
not perform that reset or authorize one.

The [store validator](../src/control_plane_kit_operations/postgres/effect_outcome_store.py.md)
uses fixed-size keyset pages, validates count before a count+1 membership query and
reconstructs the outcome with events and observation bodies. The
[current-data validator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py)
invokes it after attempt and intent checks and maps handled row errors to drift.
This semantic layer detects invalid small preimages that SQL length/profile checks
alone admit. The test does not inject concurrent writers or prove lock timing across
two sessions.

The final test reads OPERATIONS_TABLE_ATLAS.md and POSTGRES_READ_CARDINALITY.toml.
It requires both relation headings, the seven foreign-key names, a paired relation
string, direct post-transition wording and absence of direct terminal outcome.
It selects exactly three ordered read-accounting entries: get, then private current
validation occurrences one and two. All must mention LIMIT observation_count plus
one, and the first validator entry must mention fixed-size identity keyset batches.
These are selected textual/accounting checks, not an audit of every atlas statement
or proof of source-to-inventory exhaustiveness.

The inspected [atlas](../../../../control-plane-kit-operations/OPERATIONS_TABLE_ATLAS.md)
and [read accounting](../../../../control-plane-kit-operations/POSTGRES_READ_CARDINALITY.toml)
contain those entries. Their historical handoff and restart terminology must not
be substituted for current source or executed acceptance. In particular, raw
unexpected driver faults and retained bounded preimages have limits documented in
the store companion; these prose assertions do not establish universal redaction,
provider certainty, generalized recovery or safe retry.

Read depth: fresh complete 511-line test; selected actual relation columns/checks,
eight keys/seven foreign keys in SQL and current contract, full installer, selected
current-data call path and full outcome store retained; full outcome fixtures,
contract/PostgreSQL suites and unit of work retained from adjacent notes. Selected
atlas/read entries were read. This is not a full audit of the entire schema contract,
catalog verifier or all inherited fixtures. No application imports, tests, SQL,
database/provider/credential operations or source changes accompanied this note.
