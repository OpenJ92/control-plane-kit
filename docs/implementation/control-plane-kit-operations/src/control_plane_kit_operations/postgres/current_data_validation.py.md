Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py).
Maintain this document alongside its source file. When selected rows, validation/delegation order, bounds or caller assumptions change, verify and update this companion in the same change.

This internal retained-data verifier exposes no wildcard API (`__all__ = []`).
`validate_current_rows(connection)` checks selected current-schema semantic
truth using reads and reconstruction; it does not install schema, backfill
projections, repair rows, migrate data or inspect a runtime provider. Its local
connection protocol names only `execute`. It neither starts a transaction nor
acquires the installation locks itself.

## Ordered checks

First `_scan` selects authored graph versions referenced by workspace current
or desired pointers, activity-plan base/desired graphs, or desired-topology
draft revisions. A UNION removes duplicate graph IDs. Keyset paging orders by
graph ID, advances from the last returned ID, and fetches at most 64 rows per
page. This is not a scan of every retained graph or arbitrary projection.

SQL gates graph/workspace/creator text to 1–2048 octets and object-shaped graph
JSON to 1 MiB, returning null rather than oversized values. Python checks exact
row arity, literal-True gate flags, selected exact types, positive version via
GraphVersionRecord and canonical timestamp decoding. It then reconstructs the
expected identity projection using
[RealizedGraphProjectionRecord.identity_for_authored](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py),
which decodes/encodes the graph and derives its digest and identity. Authored
metadata is not selected or validated by this scan.

The identity lookup compares both the deterministic projection ID and the
semantic workspace/source/kind/key identity. `(1, 1)` counts mean exactly one
matching row; collisions or nonexact present rows fail. Importantly, `(0, 0)`
returns False and `_scan` does not use that return value: an absent identity
projection is allowed here. It is not created, nor does this helper certify
that every referenced graph has an identity projection. The later reference
query independently checks selected workspace/plan projection lineage; that
selected projection need not be of identity kind.

Next four owning validators run in order: effect attempts, retained attempt
intents, effect outcomes, and saved-preparation sources. Their stores own the
row codecs and cross-record laws. The selected entry points use paged reads;
intent validation also rejects orphan evidence, outcome validation reads
observation memberships, and saved sources reconstruct session/source/revision
records. This companion does not duplicate or claim a fresh full audit of
those owners' codecs or SQL.

Finally `_VERIFY_REFERENCES` requires one True result for workspace
authored/projection null pairing, same-workspace/source lineage and nonnegative
revision, and analogous plan/session/base/desired linkage. It is not a generic
all-foreign-key verifier. `_VERIFY_APPROVAL_SUBJECTS` separately checks closed
activity-plan or rotation subject shapes and exact review digests. Activity
plan payload/digest derives from its plan ID. Rotation payload/digest is
reconstructed from the joined rotation's fixed fields, roles, identifier and
timing bounds. These are retained-subject consistency checks, not a new user
approval or authorization decision.

## Failure, cost and transaction boundary

TypeError, ValueError and OperationsRecordError from the graph scan/delegated
validators become `CurrentRowDrift` using `from None`. That suppresses displayed
chaining; it does not promise removal of the underlying exception context from
the exception object. Failed final Boolean observations also raise this marker.
Driver errors and other exception types are not all normalized here; the two
final queries are outside that conversion handler.

The [schema caller](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
first verifies the exact schema and namespace adjuncts, then calls this
validator. `install_schema` owns the transaction context, namespace advisory
lock and SHARE locks on current relations. It converts observed row drift to
exact-current failure (reset-required for an existing namespace), while other
ordinary installation failures get a fixed generic error. Neither error grants
reset/repair authority. Calling this internal verifier directly does not inherit
those locks or a stable multi-query snapshot.

64-row pages, SQL transport gates and small result shapes bound individual
transfers, not total database work, scan duration or all delegated payloads.
The reference/approval queries and projection aggregates may still inspect
substantial retained data. No timeout, total-row cap, retry or mutation is
implemented here.

## Evidence and maintenance

Selected installer tests in
[test_current_schema_installation.py](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
cover query-only reentry, outer-transaction rollback, simultaneous empty
installers, relation-lock timeout and fixed driver-failure redaction. Selected
[test_postgres_schema.py](../../../../../../control-plane-kit-operations/tests/test_postgres_schema.py)
cases cover transactional install, repeated-install preservation and rejection
of altered approval/rotation schema without repair; those schema-drift cases
must not be relabelled as proof of every semantic query here.
The orphan-intent case in
[test_postgres_effect_attempt_intent_schema.py](../../../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_schema.py)
calls this validator and expects CurrentRowDrift; its separate source-text test
checks delegation wiring, not all runtime branches.

Review depth: full 384-line source/full 105-line schema caller, selected graph-record
constructors and all four delegated verifier entry points, selected test bodies
as described, and retained temporal codec context. The complete schema test
suites and delegated stores were not newly reviewed in full. No tests, imports,
SQL or runtime/provider operations were executed. Future edits must preserve
actual selected-data and caller-lock boundaries rather than silently making
this a migration, general repair or provider-truth authority.
