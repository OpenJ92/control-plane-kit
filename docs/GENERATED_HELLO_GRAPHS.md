# Generated Hello Graphs

The generated Hello corpus turns a tiny application into deterministic topology
data. It is an acceptance fixture for the graph compiler, validator, planner,
executor, and future control-plane instance API.

```python
from examples.generated_hello_graphs import (
    HelloGraphShape,
    generated_hello_graph,
)

graph = generated_hello_graph(
    HelloGraphShape(branching_factor=2, depth=2)
)
```

The shape above produces:

```text
7 Hello application nodes
6 Postgres data nodes
12 socket connections
```

Each non-leaf Hello node has `branching_factor` dependencies. Every dependency
is a product of one downstream HTTP connection and one Postgres connection.
The generator therefore exercises fan-out, startup ordering, environment
binding, retained data, health, and teardown planning from one small language.

Generation is deterministic and bounded. The same shape produces the same graph
descriptor, and shapes above 128 application nodes are rejected before graph
construction.

## Invalid Graphs

Invalidity is explicit data rather than hidden random mutation:

```python
invalid = generated_hello_graph(
    HelloGraphShape(2, 1),
    MissingDatabaseConnection(),
)
```

The closed fixture vocabulary currently includes:

- `MissingHttpConnection`
- `MissingDatabaseConnection`
- `DuplicateRequirementConnection`
- `CorruptEnvironmentAssignment`

These values deliberately create compiled graph data that the validator must
reject with structured `ValidationCode` evidence. They are not recipes the
runtime may attempt to repair.

The corpus is deterministic rather than randomly fuzzed so failures remain
reproducible, reviewable, and suitable for durable scenario evidence. Random or
property-based generation can be added later as another interpreter over the
same bounded shape language.

