# Postgres Unit Of Work

`control_plane_kit_operations.postgres.PostgresUnitOfWork` is the transaction
boundary for one operator command.

The controlling law from [ADR 0008](adr/0008-transactional-data-engineering-policy.md)
is:

```text
one operator command = one explicit Postgres transaction boundary
```

## Ownership

```text
application command service
  owns PostgresUnitOfWork
    owns one transaction connection
      vends PostgresStoreBundle
        workspace and graph stores
        activity and execution stores
        observed-state store
        product and authority stores
        secret-reference and authorization stores
```

All stores in one UnitOfWork share its connection. Stores receive an
execute-capable connection and never call `commit()` or `rollback()`.

## Composition

Connection creation is an application-composition concern:

```python
import psycopg

from control_plane_kit_operations.postgres import PostgresUnitOfWork


def unit_of_work(database_url: str) -> PostgresUnitOfWork:
    return PostgresUnitOfWork(lambda: psycopg.connect(database_url))
```

Application services request commit only after the complete command has written
all domain truth and operation evidence:

```python
with unit_of_work(database_url) as work:
    work.stores.graphs.save(graph_record)
    work.stores.workspaces.set_desired_graph(workspace_id, graph_id)
    work.stores.activity_history.add_action(action_record)
    work.commit()
```

The physical commit occurs on clean context exit. A clean exit without a commit
request rolls back. An exception, including one after a commit request, rolls
back. Every exit closes the connection and invalidates the store bundle.

## External Effects

Postgres cannot atomically commit Docker, Cloudflare, filesystem, HTTP, health,
or secret-provider effects. The required shape is:

```text
short transaction: record durable intent
  -> commit
    -> bounded external effect
      -> short transaction: record result, event, and observation
```

Never hold a Postgres transaction or lock across an external effect. Ambiguous
effect outcomes belong to recovery and fencing, not to a long transaction.

## Laws

- one UnitOfWork owns one connection;
- all participating stores share that connection;
- stores never commit independently;
- application command services request commit explicitly;
- uncommitted and exceptional exits roll back;
- completed UnitOfWork stores cannot be reused;
- lock order is global and session-scoped commands serialize durable identity
  before lifecycle and workspace truth;
- credentials and secret values never enter operations persistence or logs.
