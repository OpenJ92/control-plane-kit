Source: [control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Transaction participation is inspectable language

This module declares service participation and external-effect placement as pure
values. It opens no connection, controls no commit and invokes no worker or
provider. `ServiceTransactionBoundary` describes one role; `UnitOfWorkBoundary`
combines those declarations with a deployment program. The actual Operations
[Postgres unit of work](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
owns connection commit, rollback and close behavior independently.

Admission checks typed roles/enums and Boolean flags. Read-write participation
requires transaction ownership. Although `INSIDE_TRANSACTION` exists in the
effect-policy vocabulary, every service declaration using it is rejected.
After-commit effects require transaction ownership; runtime authority requires
that after-commit policy. Read and authorization roles cannot use a worker or
runtime authority. These are compatibility checks, not a complete fixed policy
table: other permitted combinations are not inferred to be the intended
configuration for a particular service, and no runtime capability is granted.

A combined boundary requires a tuple of unique rules covering every one of the
nine [program roles](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py),
matching the program's role set. It normalizes rules into enum order and offers
typed lookup. The constructor does not validate the two free-form descriptive
fields. Their defaults, `operator-command` and `stores-never-commit`, name the
intended laws; passing those strings does not enforce them in a database.

Descriptors expose the program and all rule fields. Decoding rejects extra or
missing keys and expects lists, mappings, text, enums and actual Booleans at the
specified boundaries, then reruns construction checks. The two descriptive
fields need only be text on decoding; neither is restricted to its default
literal. There is no aggregate size cap, canonical-byte codec or general
redaction guarantee here. Nested service names/parameters and enum error text
can retain caller input, and wrapped value errors retain their causes.

Full 260-line owner and full 125-line
[governing test](../../../../../../control-plane-kit-core/tests/test_unit_of_work_boundary.py)
read, with the full imported service-language owner and actual Postgres unit of
work inspected for ownership context. This documentation does not establish
that every application path follows the declaration, that a provider effect
occurs after a successful commit, or that an uncertain effect is safe to retry.
No executable validation or durable mutation was performed.
