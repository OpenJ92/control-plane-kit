# Postgres Unit Of Work

`PostgresUnitOfWork` is the transaction boundary for one operator command.

The controlling law from [ADR 0008](adr/0008-transactional-data-engineering-policy.md) is:

```text
one operator command = one explicit Postgres transaction boundary
```

## Ownership

Database ownership is per deployed control-plane server:

```text
Hub server                  -> Hub database
ControlPlaneInstance A      -> Instance A database
ControlPlaneInstance B      -> Instance B database
```

Instances do not share one application database. Each server composition
injects a connection factory for that server's own database. The
`PostgresUnitOfWork` opens transactions only through that injected factory; it
does not discover instances or select among global database URLs.

```text
application command service
  owns PostgresUnitOfWork
    owns one transaction connection
      vends PostgresStoreBundle
        workspace store
        graph topology store
        activity history store
        observed state store
        instance registry store
        secret reference store
```

Store adapters own SQL for their source-of-truth tables. They do not own the
operator command and therefore cannot decide when that command is complete.

The type boundary reflects that distinction:

```python
class PostgresConnection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


class TransactionalPostgresConnection(PostgresConnection, Protocol):
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
```

Stores receive the execute-only protocol. `PostgresUnitOfWork` receives the
transactional protocol.

## Command Shape

Connection creation remains an application-composition concern. The package
does not retain a database URL or import a Postgres driver in its algebra-only
installation.

```python
import psycopg

from control_plane_kit.stores import PostgresUnitOfWork, WorkspaceRecord


def unit_of_work(database_url: str) -> PostgresUnitOfWork:
    return PostgresUnitOfWork(
        lambda: psycopg.connect(database_url),
    )


def create_workspace(database_url: str, workspace_id: str, name: str) -> None:
    with unit_of_work(database_url) as work:
        work.stores.workspace.create(
            WorkspaceRecord(workspace_id=workspace_id, name=name)
        )
        work.commit()
```

Application services must call `commit()` explicitly. That call requests
publication; the physical Postgres commit occurs only when the complete context
exits cleanly. A clean exit without a commit request rolls back. An exceptional
exit, including one after a commit request, also rolls back. Every exit closes
the connection and invalidates UnitOfWork access to the bound store bundle.

The explicit commit belongs at the end of the complete command:

```python
with unit_of_work_factory() as work:
    graph = work.stores.graph_topology.save(graph_record)
    work.stores.workspace.set_desired_graph(workspace_id, graph.graph_id)
    work.stores.activity_history.add_action(action_record)
    work.commit()
```

If any write fails, none of those facts become visible.

## Laws

```text
one UnitOfWork owns one connection
all stores in that UnitOfWork share that connection
stores never call commit or rollback
application command services commit explicitly
commit requests do not publish before clean context exit
uncommitted exits roll back
exceptional exits roll back
finished UnitOfWork stores cannot be reused
credentials do not enter descriptors, plans, events, or logs
```

## Unit Of Work Versus Saga

UnitOfWork protects truth contained in one Postgres transaction:

```text
workspace + graph version + operation action + activity plan
```

It cannot make external effects atomic:

```text
start Docker container
switch router target
call a block control route
change cloud infrastructure
```

Those effects belong to Roadmap 0008 activity/saga execution. The database
transaction records intent and progress; the saga exposes retries,
compensation, and partial failure rather than pretending external work can be
rolled back by Postgres.

## Forward Schema Evolution

`install_schema()` is an idempotent forward installer. Roadmap 0007 adds
nullable idempotency and intent-fingerprint columns to pre-existing operation
tables, then creates partial unique indexes:

```text
(workspace_id, session idempotency key)
(session_id, action idempotency key)
```

Null keeps records written by the pre-command scaffold readable. New command
services must populate both values. The fingerprint is a digest used to detect
conflicting key reuse; it is not a second copy of the command payload.

This migration is intentionally additive and has no automatic down migration.
Dropping these columns or indexes would discard retry evidence and can make a
previously safe request execute twice. Recovery therefore means restoring a
database snapshot or applying an explicitly reviewed compensating migration,
not asking a store adapter to reverse schema installation.

Per-session action ordinals are allocated after locking the owning session row
with `FOR UPDATE`. The lock belongs to the caller's transaction and is released
only by the UnitOfWork commit or rollback. Store methods do not commit it.

## Testing Contract

The Docker-first suite uses real Postgres to prove:

- an independent connection cannot observe writes before commit;
- workspace, graph, session, action, and plan rows appear together after
  commit;
- a late SQL constraint failure removes every earlier write; and
- store adapters do not commit independently.
- schema installation upgrades the previous operation tables in place; and
- concurrent action writers publish distinct per-session ordinals.

Run:

```bash
./test.sh
```
