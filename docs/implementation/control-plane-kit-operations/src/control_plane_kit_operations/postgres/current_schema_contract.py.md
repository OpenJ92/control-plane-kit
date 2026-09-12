Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
Maintain this document alongside its source. Recheck the corresponding DDL,
catalog projection, installation laws and table atlas when changing this value.

This module owns the expected semantic shape of the current Operations PostgreSQL
schema. It defines five frozen, slotted dataclasses, constructs one large literal
CurrentSchemaContract and records a SHA256 string. Its only imports are future
annotations and dataclass. It contains no catalog reads, SQL execution, connection
creation, migration sequence or runtime hash calculation. Importing it constructs
Python values; it does not establish that any database satisfies them.

The four member tuples distinguish different kinds of schema facts:

- RelationContract describes table name/kind, persistence, access method, replica
  identity, partition and row-security flags, and trigger/policy/rule indicators.
- ColumnContract describes owning relation and name, type namespace and formatted
  type, nullability, identity/generated flags, collation and default expression.
- ConstraintContract describes owning relation/name/kind, validation and deferral,
  inheritance, ordered local and referenced columns, referenced relation, foreign-key
  update/delete/match codes, and check expression.
- IndexContract describes relation/name/owning constraint, access method, uniqueness,
  primary/valid/ready/live/immediate flags, clustering, replica identity, null handling,
  ordered key/include entries, operator classes, collations/options and predicate or
  expression text.

CurrentSchemaContract groups those tuples. The dataclasses supply no __post_init__
validation, domain-code decoding or canonicalization. Type annotations do not
enforce tuple members or reject wrongly typed constructor arguments. The actual
constant uses tuples and scalar values; frozen/slotted declarations should not be
treated as a general validator for arbitrary constructed contracts.

The reviewed declaration inventory has 40 relations, 507 columns, 382 constraints
and 131 indexes. The constraint entries comprise 40 primary keys, 46 unique
constraints, 209 checks and 87 foreign keys. Relations cover workspace/authored and
realized graph lineage, drafts, sessions/plans/approvals, execution runs/requests/
receipts/events, attempts/intents/outcomes and compensation, gateway/key/control
records, observations, products, runtime/image/ingress authority and secrets.
These counts describe this version's expected catalog, not runtime row counts or
independent application capabilities. Updating counts alone is never a schema proof.

The [catalog verifier](current_schema_verification.py.md) converts the constant's
dataclasses with asdict and serializes four expected arrays at import time. It
compares them with selected PostgreSQL catalog projections. Relation order is
name order; constraints/indexes use relation/name order; columns use relation and
physical attribute order. Array order matters even though JSON object keys are
sorted. Dropped-column gaps are not explicit fields, but surviving column order is.

This comparison interprets literal strings as PostgreSQL adapter data. A check
expression is compared through the verifier's deparser representation, including
its specific quoted-position normalization, not by proving logical equivalence.
Foreign-key code a means NO ACTION; deferrable and deferred remain separate flags.
Index key order, include entries, predicates, qualified operator classes/collations
and option values are all significant. Trigger/policy/user-rule integers in the
current verifier are zero-or-one existence indicators, not arbitrary object counts.
Ownership, ACLs, tablespaces and storage options are not modeled by these types.

Selected entries illustrate why these distinctions matter. Activity-run identity
has an explicit COLLATE "C" grammar check. The draft head references its exact
workspace/draft/revision with deferrable=True and deferred=True. Outcome membership
has a relation-wide observation unique constraint that is deferrable but initially
immediate, while its outcome identity/count foreign key is initially deferred.
The corresponding observation index has immediate=False. These are distinct
facts; a generic claim that every relationship is immediate would be wrong.

The [installer](schema.py.md) derives the expected relation lock list from this
constant. In an empty owned namespace it executes
[current_schema.sql](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql),
then checks the result. For an existing namespace it verifies current structure
and retained data without incremental migration or repair. Catalog conformity is
only one part of acceptance: namespace adjuncts and selected retained-row laws
are separate checks. This value does not own transaction policy, authorization,
application-state transitions, provider truth or permission to reset existing data.

CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256 is
6dff163cf72add13406d168d8e7389cdada4e6885e345c2534307753c5d24f4c.
It is a checked-in value, not automatically recalculated when a tuple is edited.
The [installation tests](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
check it against both a fixed expected string and SHA256 of compact, sorted-key,
ASCII JSON containing domain control-plane-kit.operations.postgres.current-schema,
format_version 1, and dataclasses.asdict(contract). This is a semantic-envelope
digest, not the hash of this Python file or of the DDL bytes. The SQL hash is a
separate assertion. The verifier does not use this SHA as a runtime admission token.

Those tests also check selected receipt, authority and event vocabulary, fresh
installation, selected schema drift and existing-schema preservation. The
[outcome-schema tests](../../../tests/test_postgres_effect_outcome_schema.py.md)
inspect selected keys, composite foreign keys and checks; they do not exhaust all
properties of every contract entry. The
[atlas tests](../../../tests/test_operations_table_atlas.py.md) compare their header,
relation order, foreign-key ledger and graph with this constant. Their imported SHA
comparison does not independently recompute the digest, and their prose checks do
not make every atlas ownership statement true.

Read depth: all type definitions/imports/top-level declaration structure and hash
were inspected; text-only inventory counted the four declaration families and
constraint kinds and displayed all relation names. Column/default examples, run
checks, deferred draft/outcome relationships, authorization foreign keys and index
entries were selected actual literal reads. This is not an individual audit of all
1,060 member declarations or all 392 KB of source text. Full verifier and installer,
all installation-test bodies with selected helpers, full outcome-schema and atlas
tests, and selected actual DDL were retained from the preceding reviews. PostgreSQL
package exports were read: they expose installation/stores, not this contract value.

Before modifying a relation family, inspect its complete affected literal entries,
DDL and actual owning stores/tests together. This companion explains ownership and
comparison rules; it is not a substitute schema or authority to mechanically accept
new generated values. No automatic generator or regeneration command is asserted.
This documentation introduced no source, schema, credential, network or mutation
surface. No application imports, tests, SQL, database or provider operations ran.
