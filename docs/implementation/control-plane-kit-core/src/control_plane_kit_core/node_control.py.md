Source: [node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py).
Maintain this companion with source and relevant imported contract changes.

This pure owner contains the existing variable/command language and the shared
workload surface declaration. NodeHealthReadKind adds only liveness/readiness
names. A surface contains nonempty variables or health_reads or both. Variables
retain their total read/apply codec contracts; health does not fabricate one.
The optional tuple is typed, unique and canonically ordered. Empty health_reads
is omitted so variable-only descriptor bytes stay stable; an explicit empty
wire extension fails. Both forms share the existing 16,384-byte surface budget.

health_read_path checks a typed declared kind before selecting the fixed path
from NODE_HEALTH_ROUTES. The surface's provider socket supplies endpoint identity.
HEALTH_CHECKABLE does not select this route, and the method accepts no endpoint,
URL, credential or callback. products/algebra enforce capabilities and graph
validation proves HTTP provider existence. These values perform no I/O.
The new declaration tests and existing variable/surface tests own those laws.

Core #1826 adds the RUNTIME graph-reference role for an independent nominal
health-request runtime binding. NodeControlTarget and all existing descriptor
fields stay unchanged. A syntactically valid reference is not proof of graph
membership; Operations and trusted workload composition own that fact.
