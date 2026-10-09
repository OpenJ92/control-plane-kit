Source: [http.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py).
Maintain this companion alongside its source.

Declares the authenticated GET workload verifier read with the existing READS role, read-only effect and bounded response contract. The closed CSV purpose selector is a named path parameter; no query-decoder extension or mutation route is introduced.

Receiver authoring context declares one authenticated read-only GET with named
16-KiB logical request and 1-MiB response schemas. Other GET request schemas
retain EmptyRequest/1,024 bytes. Schema metadata does not add GET-body support
or decode query parameters; Operations owns combined scopes and Servers #238
owns actual transport/adoption.
