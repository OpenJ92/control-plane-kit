Source: [control-plane-kit-core/src/control_plane_kit_core/lifecycle.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/lifecycle.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Resource lifecycle values

This file models ownership, compute persistence and independently named data
resources as a product. Data defaults to retained; the common owned-ephemeral
constant says nothing about deleting data. Data-resource identities must be
unique within a lifecycle and are sorted for deterministic descriptors.
Attached/external ownership requires retained compute and no declared owned
data.

These are descriptive values consumed by
[graph representation](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py) and
[planning](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py). They do not delete
resources, prove external ownership or grant destructive permission.
Changing a lifecycle flag must not become an implicit adoption/cleanup command.

[test_resource_lifecycle.py](../../../../../control-plane-kit-core/tests/test_resource_lifecycle.py)
joins the value with graph codec and planning: ordinary topology removal does
not synthesize data destruction, retained compute avoids resource removal,
external resources receive no lifecycle work, and policy changes become review
blockers. The critical/destructive requirements for explicit data-destruction
activities belong to the activity-plan owner, not this dataclass. Runtime
cleanup and retained-data survival need external evidence beyond these tests.
