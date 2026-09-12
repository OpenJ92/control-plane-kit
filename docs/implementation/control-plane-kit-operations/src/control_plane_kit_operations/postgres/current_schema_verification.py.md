Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_verification.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_verification.py).
Maintain this document alongside its source file. Recheck the frozen contract,
installer, retained-data validator and governing installation tests when it changes.

This 627-line module observes PostgreSQL catalogs and compares their selected
meaning with a frozen current-schema value. Its four public functions return
booleans: namespace_is_object_free, expected_relations_are_present,
current_namespace_adjuncts_are_exact and current_schema_contract_is_exact. They
execute catalog queries on a caller-supplied connection. They do not install,
migrate, repair or reset a schema, commit a transaction, acquire the installer's
locks or validate retained application rows.

The mathematical shape is a projection and comparison:

```text
current_schema() catalogs
  -> bounded candidate relations, columns, constraints and indexes
  -> ordered semantic JSON projections inside PostgreSQL
  -> equality with imported frozen contract arrays
  -> four booleans
  -> one Python acceptance boolean
```

“Exact” is relative to the fields represented by this projection. It does not
mean every PostgreSQL property is modeled. Ownership, ACLs, tablespaces and storage
options, for example, are not fields of this contract. The module's local
PostgresConnection Protocol declares execute returning object; the implementation
also requires that returned object to provide fetchall. The annotation is not a
runtime connection or cursor validator.

The [contract owner](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
defines frozen, slotted dataclasses for relations, columns, constraints and indexes,
collected in CurrentSchemaContract. Those definitions have no constructor
validation. The verifier trusts CURRENT_POSTGRES_SCHEMA_CONTRACT, converts each
tuple to asdict records and serializes four JSON arrays at module import. JSON
uses ASCII escaping, sorted object keys and compact separators. Array order is
preserved; sorting object keys does not sort the contract's records.

The companion tests pin 40 relations, 507 columns, 382 constraints and 131 indexes.
Accordingly, this source passes candidate limits of 41, 508, 383 and 132, followed
by each expected count and its JSON array. It does not verify the contract's SHA
constant at runtime. The frozen literals, their hash provenance and the DDL
program have separate owners; this note does not reproduce their entire content.

All four queries scope namespace ownership through current_schema(), derived from
the caller's search path. There is no schema-name argument or independent check
that current_schema() names an existing intended namespace. Objects in other
schemas are outside that ownership filter. This is not a database-wide absence
or consistency assertion.

namespace_is_object_free queries fourteen catalog families: relations, routines,
types, collations, conversions, operators, operator classes and families, extended
statistics, text-search configurations, dictionaries, parsers and templates, and
extensions. Each absence predicate uses NOT EXISTS with LIMIT 1. The result is
one boolean, not a list of discovered objects. No special allowance for table
row types or indexes is needed here: any object in the selected families makes
the namespace nonempty.

expected_relations_are_present only counts named ordinary tables, relkind r,
against the expected relation-name list. It establishes that every expected
table is present, not that there are no extras or that its columns and constraints
are correct. Its role in the installer is to reject missing tables and replacement
views before attempting locks on the expected table names.

current_namespace_adjuncts_are_exact is a complementary absence check. It allows
relation kinds r, p, v, m, f, i and I through this stage, while rejecting other
relation kinds such as sequences. Its type predicate rejects types with both
typrelid and typelem zero, leaving relation-backed types and array types outside
that rejection. It also rejects the other namespace-owned catalog families
checked by the object-free query. Passing this helper alone does not prove the
main relation contract: a view may pass its relation-kind allowance and still
fail the main comparison.

These three scalar helpers use _exact_boolean. It accepts a list or tuple
containing exactly one list-or-tuple row with exactly one built-in bool. False is
a legitimate negative observation; malformed result shape raises RuntimeError
with the fixed text "catalog observation shape". Driver exceptions propagate.

The main query uses four MATERIALIZED candidate CTEs. Relations include ordinary,
partitioned, view, materialized-view and foreign-table kinds. Columns, constraints
and indexes join the candidate ordinary tables. Candidate sets are ordered and
limited to the corresponding expected cardinality plus one. This admits one
overflow witness without transporting an arbitrary catalog inventory to Python.
The final equality checks also require the exact expected count.

Relation projection records name, kind, persistence, access method, replica
identity, partition status, row security and forced row security. It includes
non-internal triggers, policies and user rules other than _RETURN as zero-or-one
existence indicators, despite their integer field names. They are not actual
counts of arbitrary numbers of triggers, policies or rules. The current relation
entries expect their absence.

Column projection records relation and column names, type namespace and formatted
type, nullability, identity/generated flags, collation identity and deparsed
default expression. Dropped and nonpositive attribute numbers are excluded. The
candidate limit is applied in relation/name order, while final JSON aggregation
uses relation/attribute-number order. Attribute numbers are not themselves JSON
fields, but the relative physical order of surviving columns affects equality.

Constraint projection records names, kind, validation/deferral/inheritance flags,
ordered local and referenced column names, referenced relation, foreign-key actions
and match type, and check expression. Key arrays are expanded with ordinality;
their cardinalities are compared with the projected arrays. A referenced relation
is named only when its namespace equals current_schema(). A cross-schema reference
therefore cannot masquerade as the same local table name in this field.

Check expressions use pg_get_expr without pretty printing and replace the exact
quoted token '"position"' with 'position'. This is a specific textual normalization,
not a general proof that two SQL expressions are logically equivalent. Foreign-key
action and match blanks become null. The comparison preserves the remaining
deparsed expression strings as contract data.

Index projection records name, owning primary/unique/exclusion constraint when
present, access method, uniqueness and primary status, validity/readiness/liveness,
immediacy, clustering, replica identity and nulls-not-distinct behavior. It separates
ordered key entries from included entries using pg_get_indexdef, retains qualified
operator classes and collations, and includes option flags, predicate and expression
text. Collation OID zero becomes null. Operator-class, collation and option array
cardinalities must each match the number of key attributes.

The four final subqueries compare ordered jsonb aggregates with the expected
arrays. Relation order is by name; constraint and index order is by relation/name;
column order is by relation/attribute number. COALESCE turns null comparison
outcomes into false. Only the resulting four booleans leave this query; catalog
object names, SQL definitions and arbitrary row data are not returned as a report.

current_schema_contract_is_exact accepts exactly one list-or-tuple row containing
four built-in True booleans. Unlike _exact_boolean, malformed result shape returns
False rather than raising. Integers equal to True do not qualify. SQL or driver
exceptions still propagate. Direct callers receive this distinction without the
installer's error classification or redaction policy.

Bounds apply to downstream candidate cardinalities and the result sent to Python.
They do not bound total catalog scan/sort cost, individual deparsed expression
length, total database memory or query duration. There is no statement timeout in
this owner. PostgreSQL catalog fields and deparser output are part of the adapter's
compatibility assumptions; this is not portable SQL or an automatic contract
refresh mechanism.

The actual [installer](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
supplies the effect boundary. It checks that autocommit is a bool, enters the
connection's transaction context and acquires a transaction-scoped advisory lock
derived from database and schema identity. An object-free namespace receives the
DDL program, relation locks and verification. An existing namespace must first
contain the expected ordinary tables, then receives relation locks and verification.
The relation locks use LOCK TABLE ONLY in SHARE mode across the expected names.

Installer acceptance composes the main contract comparison, adjunct absence and
[current-data validation](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py).
That validator reads referenced authored graphs in batches, reconstructs values,
checks current pointers and approval subjects, and delegates selected retained-row
checks to their owners. It is a separate layer from catalog shape. Its graph scan
computes identity-projection checks, but does not reject merely because the helper
returns False for an absent identity projection; later reference checks establish
their own required pointer relationships. This is not an assertion that every
historical row or every possible application invariant is exhaustively checked.

For an existing schema, a negative acceptance result becomes "operations schema
reset is required". Unexpected observation/driver failures become "operations
schema installation failed". Failed verification after fresh DDL also takes the
generic failure path. SchemaInstallationError is raised with fixed text and no
chained cause/context. This classification belongs to the installer, not these
queries. In particular, the main helper's malformed result False can become reset
advice, whereas malformed scalar results raise and take the generic failure path.
Reset advice never performs a reset. Existing-schema verification does not repair
drift, and the transaction context retains caller transaction authority.

The governing [current-schema installation suite](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
contains seven static tests and seventeen database-backed tests. Static checks
pin cardinalities and hashes, selected current vocabularies and receipt constraints,
absence of historical residue and the unconditional DDL shape. These assertions
are source/contract evidence, not evidence that this documentation pass installed
or verified a database.

Database cases cover fresh installation, repeated installation with row and object
identity preservation, selected constraint/column/index drift, expected-table
replacement by a view, unrelated-schema preservation, rollback after injected
failure, caller-owned outer transactions, concurrent installers and relation-lock
timeout followed by retry after release. Generic driver failures are checked for
fixed error text and absent secret-bearing cause/context. The recording connection
checks that existing-schema paths issue no DDL/DML statements; those paths still
use transaction and relation locks.

Coverage has specific limits. The seven stray-object families are each installed
into an otherwise noncurrent namespace; these cases do not independently isolate
the adjunct predicate on an otherwise complete current schema. The revision-history
index test reuses this verifier's candidate/semantic CTEs and compares two index
records field by field with the contract. It is useful composition evidence, not
an independent implementation of catalog projection. The suite does not exhaust
every projected catalog field, malformed result shape or catalog resource bound.

Selected [Postgres foundation tests](../../../../../../control-plane-kit-operations/tests/test_postgres_schema.py)
add caller rollback, incompatible-schema preservation, repeated-install retained
rows and constraint identities, approval-column drift and rotation-status constraint
drift without repair. Those drift cases alter schema structure; their names do not
by themselves establish exhaustive retained-data validation coverage.

Read depth: full verifier627, installer105, current-data validator384 and current
installation test1351/all24 were read. Contract dataclass definitions, representative
relation/index entries and hash declarations were selected reads; its complete
literal inventory was not independently reread. Foundation tests were read through
the rotation-status case at line224; remaining cases and their seeding helpers
were not reviewed here. Delegated retained-row store implementations and the full
DDL program were not independently reviewed for this note.

Security: this documentation introduces no runtime, network, credential or mutation
surface. The source's bounded boolean result is distinct from direct driver-error
handling, transaction coordination and application-row validation. Validation for
this note is static link/whitespace checking and frozen-source comparison only.
No imports, executable tests, SQL, schema installation, database or provider calls
were performed. Independent review, inventory and publication remain separate.
