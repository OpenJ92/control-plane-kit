Source: [control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Persistence laws without persistence machinery

This module declares eight durable-store kinds, three mutation subjects crossed
with eight phases, and five persistence handoff kinds. Frozen contract values
name participation, ordering, candidate requirements, failure visibility and
enforcement ownership. They contain no database, repository, mutable holder or
cleanup implementation, and do not execute a plan or authorize a mutation.

Each store kind has one fixed service role and ordering policy. Constructors
require read-write participation, no accepted secret values, stores-never-commit
and Operations enforcement. For example, activity history and execution journal
declare append-only ordinals, observed state declares latest-by-time-then-ID,
and the operation ledger declares scoped idempotency. These are named laws, not
a schema or exhaustive list of current Operations store classes. The actual
[store bundle](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
contains additional concrete owners.

Mutation declarations require typed subject/phase/failure-policy values, Boolean
flags, no published values and Operations enforcement. Publish, replay identity
and cleanup-superseded phases require a candidate. Other phases may also request
one. The constructor accepts any closed failure-visibility policy for a phase;
the canonical factory chooses prior-projection preservation for preparation and
validation, rollback for publication, visible uncertainty for cleanup/preservation
and bounded evidence for the remaining phases. No candidate object, rollback or
failure report is produced here.

Handoffs forbid Core database drivers/schema DDL and require Operations ownership.
If a handoff requires a unit of work, it must also require a caller-owned
transaction. The canonical factory sets both requirements true for every kind;
direct construction can set both false. This language does not inspect imports,
open a transaction or enforce how a store uses its connection.

`PersistenceBoundaryContractSet` requires typed tuples covering all store kinds,
handoff kinds and subject/phase pairs, then sorts by their textual enum values.
Coverage uses sets: duplicate entries are not rejected or removed. Thus the
canonical factory produces 8/24/5 entries, but the constructor admits larger
collections, including differently configured duplicates where individual
contracts permit them. No collection or aggregate descriptor-size cap is imposed.

Descriptors expose closed policy data with a fixed set kind. Decoders require
exact keys, lists/mappings, enum text and actual Booleans, then invoke the same
constructors. They are not canonical-byte codecs or a universal safe-error
boundary; invalid enum text can survive in wrapped errors and causes. Flags
about secret values do not scan or sanitize an external payload.

Full 598-line owner and full 154-line
[governing test](../../../../../../control-plane-kit-core/tests/test_persistence_boundary_contract.py)
read, with the actual enforcement-owner enum, previously read
[service](./services.py.md)/[transaction](./transactions.py.md) languages and
selected store-bundle declarations. Actual unit-of-work ownership was read in
the transaction review. This is not a full store/schema or mutation audit. No
tests, database actions, retained-data changes, cleanup or runtime effects were
performed for this documentation.
