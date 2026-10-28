Source: [schema.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py).

The current schema installer owns the PostgreSQL namespace advisory lock and
transaction/savepoint scope. By default it creates the existing schema program
in an object-free namespace, or verifies exact current schema and row truth.
Incompatible state is refused intact. It does not migrate, repair or reset.

#2057 adds keyword-only `require_empty: bool = False`. A non-boolean option is
refused before connection access with `operations schema installation option is
invalid`. True checks the existing emptiness predicate under the same namespace
lock and refuses every occupied namespace, including exact current state, with
`operations schema requires an empty namespace`. Both errors are bounded and
context-free. The empty create/verify program and default path are unchanged.
No table, schema version, permission state or new locking API is introduced.

Fresh-only admission is deliberately not an automatic retry operation: a second
attempt after a committed successful install refuses occupied state. It proves
only cooperative namespace admission, not resource provenance, authentication,
workspace permission initialization or protection against privileged external
writers. The caller must still own and authorize the database capability.

The existing `connection.transaction()` owns a top-level transaction when entered
on an idle autocommit connection, or a savepoint inside a caller transaction.
Returning from the latter does not commit the caller's work. Refusal/failure
rolls back the owned transaction/savepoint; an outer caller can still roll back
successful installation. Schema and later initialization commits remain separate.

The existing schema owner tests cover these boundaries. #2057 focused targets
first reached the absent keyword; source review and actual focused/full owning
Docker results must be recorded before acceptance. Servers #346 may consume only
the accepted exact Operations coordinate, without copying these semantics.
