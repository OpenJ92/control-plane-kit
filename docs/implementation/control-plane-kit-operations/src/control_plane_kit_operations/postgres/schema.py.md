Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py).
Maintain this document alongside its source file. When installation, verification, locking, error or caller-transaction contracts change, verify and update this companion in the same change.

`install_schema` creates the current schema in an object-free namespace or
verifies an already-current namespace. It does not perform incremental
migrations, repair retained rows, drop incompatible objects or reset a database.
The caller selects the connection/database/search path and must possess the
appropriate authority. A returned reset-required error is not permission to
destroy or replace anything.

The module reads packaged `current_schema.sql` at import time; SQL is executed
only through installation. It derives the relation lock list from
CURRENT_POSTGRES_SCHEMA_CONTRACT, not database discovery. The public
PostgresConnection protocol is execute-only, while the installer's private
connection protocol also requires a Boolean `autocommit` and `transaction()`.
The observed Boolean may be True or False; it is checked for type, not set here.
No connection creation, ownership transfer, explicit close, timeout or retry
policy is provided.

Inside `connection.transaction()`, an advisory transaction lock is keyed by the
current database and schema, separated by chr(31). This coordinates cooperating
installers; it is not authentication or exclusion of every arbitrary DDL actor.
The next branch is deliberate:

- Object-free namespace: execute the complete packaged SQL, acquire SHARE locks
  on every expected relation, then verify. Fresh verification failure is a
  generic installation failure, not advice that existing data needs resetting.
- Nonempty namespace: first require all expected ordinary tables to exist so
  missing/replaced relations reject before attempting the relation locks. Then
  acquire the same SHARE locks and verify. A negative verification result means
  reset-required; there is no repair path.

Verification short-circuits through exact semantic schema contract, namespace
adjunct checks and [retained-row validation](current_data_validation.py.md).
The relation-presence precheck is not itself exact-schema acceptance. The
catalog verifier owns comparison of relation/column/constraint/index semantics
against the checked-in contract; adjunct checks cover other namespace object
families. Retained-row validation owns selected graph/reference/approval and
delegated attempt/source checks, not an exhaustive proof of all historical
application semantics or provider truth.

Transaction behavior belongs to the supplied transaction context. With the
usual psycopg connection, an already-open outer transaction remains caller-owned
and installation nests rather than committing that outer work. SHARE and
advisory locks are transaction-scoped and may therefore outlive this call under
an outer transaction. Calling this function can acquire blocking locks even
when no schema changes are needed. It does not promise a wall-clock bound or
global deadlock freedom.

The outer handler records one of two fixed messages and raises
SchemaInstallationError after leaving the handler: reset-required for its
private `_ResetRequired`, generic installation-failed for other ordinary
exceptions. No raw driver message is copied to that outward error. Boolean
autocommit access failure/type rejection also yields the generic message.
BaseException cancellation is not caught by these Exception handlers; the
transaction context still owns unwind behavior. This is not a guarantee that
an ambiguous connection failure can be repaired or blindly retried.

The [foundation test companion](../../../tests/test_postgres_schema.py.md)
describes its seven real-Postgres tests: transactional/repeated installation,
selected schema-drift rejection without repair, selected closed values and
ingress-history schema shape. Additional selected bodies in
[test_current_schema_installation.py](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
cover query-only reentry, outer rollback, two cooperating installers, a relation
lock timeout and generic redacted failure. These are finite observations, not
an audit of every catalog object, concurrent actor or driver failure.

Review depth: full 105-line owner/full 535-line foundation test; retained full
384-line data validator; selected current verifier namespace/precheck/Boolean
and comparison-entry boundaries, contract types and relevant SQL constraints;
selected larger installation tests, not that whole suite or the full generated
schema/contract/query body. No installer, tests, database or provider actions
were executed. Schema policy changes require their own reviewed data decision.
