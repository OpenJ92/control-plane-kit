Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/__init__.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/__init__.py).
Maintain this document alongside its source file. When exports, eager dependencies or transaction/schema ownership change, verify and update this companion in the same change.

This 95-line initializer binds the public PostgreSQL facade to canonical schema,
store, bundle and UnitOfWork definitions. Its explicit `__all__` lists the
imported public names; there are no local service definitions, wrappers, lazy
hooks, connection factories being invoked, database queries or schema-install
calls in this file. Importing `install_schema` is not calling it.

Exports group workspace/authored/realized graph stores; activity history,
execution and compensation stores; delegation, gateway probe/rotation and
node-control attempts; product/image-pull, runtime/delivery, ingress and secret
authority stores; observations; and the schema/transaction construction types.
This is a selected facade, not every store in PostgresStoreBundle. For example,
the bundle's private node-control signing-authority collaborator is not added
to this initializer's public list.

Imports are eager and require the package's declared dependencies, including
psycopg. The [schema owner](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
reads packaged `current_schema.sql` into a module constant during import. This
is local resource loading, not SQL execution; missing package data or imported
dependencies can nevertheless prevent import. The root Operations facade also
enters this package through its explicit node-control attempt-store binding.
No lightweight, lazy or dependency-free import guarantee is made.

`PostgresConnection` is the schema owner's execute-only protocol. It is not the
whole installer contract: `install_schema` also needs the private schema
connection's `autocommit` and `transaction()` behavior. The separately exported
`TransactionalPostgresConnection` and `PostgresConnectionFactory` belong to
[UnitOfWork](unit_of_work.py.md), which owns commit/rollback/close around the
supplied connection. [PostgresStoreBundle](stores.py.md) composes stores around
that connection; neither re-exporting a store nor constructing the bundle
authenticates a command or grants direct SQL authority.

Schema installation, exact-schema verification, retained-row checks and
application transactions remain distinct responsibilities. The installer may
create an object-free owned namespace or verify the exact current schema; it
does not automatically migrate, repair or reset incompatible data. Exporting
the function is not authorization to perform installation against a database.

Evidence: selected portions of
[test_current_schema_installation.py](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
check the public connection is execute-only, selected historical exports/modules
are absent, existing-schema reinstall preserves identities without mutation
statements, and installer transaction/lock/failure behavior. They do not check
the identity of every facade export or every imported store's semantics. The
review read this full initializer, full 105-line schema caller, package metadata,
those selected tests, and retained complete bundle/UnitOfWork context; this is
not a new full review of all imported stores. No import, test, database or
provider operation was executed.

When changing the facade, preserve defining-module identity and dependency
direction, check both bindings and intended `__all__` membership, and leave
schema/data/authorization policy with its owner rather than adding initializer
side effects.
