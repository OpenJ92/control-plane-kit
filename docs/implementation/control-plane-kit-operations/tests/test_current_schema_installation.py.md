Source: [control-plane-kit-operations/tests/test_current_schema_installation.py](../../../../control-plane-kit-operations/tests/test_current_schema_installation.py).
Maintain this document alongside its source file. When current-schema contracts, installer behavior, fixtures or evidence limits change, verify and update this companion in the same change.

This 1,351-line suite has 24 methods: seven static/public-contract tests and
seventeen PostgreSQL installation tests. It protects the direct current-schema
model, not historical migration, repair or provider recovery. Its current
expected catalog is 40 relations, 507 columns, 382 constraints and 131 indexes;
these are this source revision's fixtures, not universal package constants.

## Static and public boundaries

The seven static tests inspect the execute-only public PostgresConnection,
absence of named migration/backfill exports/files/definitions and selected
historical text, the current contract's relation list/counts/digest and limited
imports, execution-command receipt columns/keys/bounds, selected node-control
purpose/intent/approval vocabulary, two uncertainty-abandonment event strings,
and the packaged SQL's allowed statement prefixes/count/hash. The vocabulary
test recomputes the contract digest from the dataclass projection plus fixed
domain/version; other checks compare declared values or exact source text.

These checks bind the inspected representation. They do not execute command
replay, recovery, secret-use authorization or node control. An event name in a
CHECK expression is not authority to perform that action. The removed-name AST
scan and SQL token/prefix checks are finite structural laws, not a general
dynamic-import or SQL-effect analyzer.

## Real database evidence

The database class requires the package Docker suite's configured
CPK_OPERATIONS_TEST_DATABASE_URL. Each method creates a UUID-named schema on an
autocommit connection and selects it through search_path; teardown restores
public, drops that exact schema CASCADE and closes. Some subtests deliberately
drop/recreate that owned schema. A cross-schema case creates another exact
temporary schema; the outer-transaction test creates an exact temporary public
marker table. These are disposable test-owned mutations, not authority to
inspect or change arbitrary persistent data.

The seventeen methods cover these distinct families:

- Fresh install checks relation names/counts and absence of a migration ledger.
  A separate revision-history case executes the packaged SQL directly, reuses
  the verifier's bounded semantic-index CTEs to expose two named index
  descriptors, compares every returned field to their contract, then calls the
  installer. It is selected actual catalog evidence, but reuses the production
  observation query rather than providing an independent all-index decoder.
- Authority vocabulary cases insert selected new purpose/intent rows and
  verify retained selected rows/object identities on reentry. The approval
  scope follow-up checks three node-control scopes fail both request and
  decision constraints with the named CheckViolation. Direct fixture inserts
  are not a real key/provider/approval service workflow.
- Drift cases exercise five drift variants across three selected authority
  constraints: pre-change, missing, wrong, extra or unvalidated; separately install a pre-Secrets
  intent expression; add an extra workspace column; or remove/change one
  timeline index. Rejection preserves the observed post-drift catalog and
  selected rows without installer repair. The altered schema is the test's
  deliberate input, not a migration performed by install_schema.
- Seven nonempty object fixtures (table, view, materialized view, sequence,
  enum, domain, function) reject before creating expected tables. A separate
  cross-schema lookalike fixture is ignored/preserved. Replacing the expected
  observations table by a view rejects before recorded relation-lock SQL.
  These are selected object families and namespace cases, not every possible
  PostgreSQL adjunct or search-path attack.
- Reinstall records queries and compares relation/constraint OIDs plus selected
  rows. `_assert_calls_are_read_only` rejects mutation SQL keywords in recorded
  strings; advisory/SHARE locks and transaction control remain allowed. Here
  “read-only” means no observed schema/data mutation statements, not lock-free,
  transaction-free or a formal proof against every SQL indirection.
- Failure injection at execute call five runs after the current fresh path's
  schema SQL and relation-lock submission; observed namespace relations,
  routines and types are empty afterward. This proves that chosen transaction
  rollback point, not all possible driver/network/commit failures. The outer
  transaction case inserts caller marker work, invokes installation and calls
  rollback, then asserts installation namespace emptiness. It drops the marker
  table without checking the marker row, so it does not independently assert
  rollback of that caller row or every outer-transaction effect.
- Two worker threads use separate connections and a barrier to install one
  empty schema, then check both finish without errors and the expected catalog.
  Barrier wait is five seconds and each join is thirty; no cancellation of a
  stuck worker or universal database statement timeout is implemented. This is
  one cooperating-installer race, not global deadlock freedom.
- A separate connection holds ACCESS EXCLUSIVE on activity events while a
  contender with 500ms lock_timeout installs. It expects the fixed generic
  error, no reset advice or mutation SQL, then explicitly retries after the
  blocker rollback and checks retained state. That chosen known lock failure
  does not authorize blind retry of an ambiguous external mutation.
- A simulated driver error with hostile connection text becomes the fixed
  public error without that text in repr. Shared assertions require exact
  SchemaInstallationError type/message and no cause/context. This is chosen
  failure redaction, not all exception/cancellation or cleanup-fault coverage.

## Fixture and observation limits

Authority fixtures directly write synthetic public-key text/fingerprints,
secret references, requested rotations and authorization rows. No secret is
resolved, key validated or provider called. `_authority_rows` compares selected
IDs/purposes/intents, not full row contents. The approval fixture deliberately
uses an empty subject and fabricated digest for subsequent SQL-scope rejection;
it is inserted after the successful reentry observation and is not proof of
semantic approval validity under another install.

`_object_identities` observes relation/constraint names and OIDs; the separate
constraint snapshot includes validation state and deparsed expressions.
`_namespace_objects` covers relation/routine/type names, not the installer's
whole adjunct census. The recording connection stores query strings and
forwards actual transactions/execute calls; it does not capture wire traffic.
Sequential teardown/extra-resource cleanup can mask earlier errors or leave
later cleanup unperformed if a step fails. The suite does not prove its own
fault-total cleanup under arbitrary interruption.

The actual [installer](../src/control_plane_kit_operations/postgres/schema.py.md)
owns transaction and advisory/SHARE-lock order. The
[catalog verifier](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_verification.py)
owns semantic comparisons using four candidate sets capped at expected count
plus one, and the
[row validator](../src/control_plane_kit_operations/postgres/current_data_validation.py.md)
owns selected retained-data checks. Catalog result/candidate bounds are not
total database cost or duration bounds. This suite does not replace the
separate [foundation tests](test_postgres_schema.py.md) or all store-specific
semantic tests.

Review depth: full test, retained full 105-line installer and 384-line row
validator, completed full catalog-verifier read, selected contract types and
relevant SQL definitions plus prior foundation-test context. The entire
generated schema/contract was not newly audited. No tests, imports, installer,
database or provider actions were executed for this note. Future changes should
update the current contract deliberately, without using test fixtures or
reset-required diagnostics as migration/destructive authority.
