Source: [http.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py).
Maintain this companion alongside its source.

#2091 declares `read.product-topology-preview` as a bounded read-only POST
query under READS/read. The generic contract permits this explicit POST query
pair while preserving GET read routes, authorization/admin GETs and command
laws. Preview request and response are capped at 1 MiB; existing draft-create
request is raised to 1 MiB to admit a recipe as an alternative to its raw graph.
This declaration supplies neither POST decoding nor product resolution.

#2041 adds authenticated read-only `GET /workspaces` with no path workspace.
It declares the bounded logical inventory request (16 KiB) and summary page
(1 MiB). The Operations durable-only profile owns eligibility and per-item
authorization; these pure route values do not grant global inventory access.

Declares the authenticated GET workload verifier read with the existing READS role, read-only effect and bounded response contract. The closed CSV purpose selector is a named path parameter; no query-decoder extension or mutation route is introduced.

Receiver authoring context declares one authenticated read-only GET with named
16-KiB logical request and 1-MiB response schemas. Other GET request schemas,
apart from the separately declared workspace inventory, retain
EmptyRequest/1,024 bytes. Schema metadata does not add GET-body support
or decode query parameters; Operations owns combined scopes and Servers #238
owns actual transport/adoption.
