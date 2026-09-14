Source: [control-plane-kit-core/src/control_plane_kit_core/topology/diff.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Validated graphs to structural changes

diff_graphs requires two ValidatedGraph values, calls require_valid on each and
checks their registered BlockSpec languages agree. A mismatch yields one
graph-level ambiguity rather than comparing incompatible representations.
Different block families sharing a node ID are identity ambiguity; runtime-kind
and implementation-kind transitions become unsupported change data.

The interpreter compares actual typed graph values and emits explicit field
subjects for authority, membership, environment, configuration, deliveries and
other changes. Authority changes are distinct from metadata changes. It then
orders changes deterministically by form, subject and descriptor; that order is
a stable report order, not the dependency schedule for provider effects.

[changes.py](changes.py.md) owns the output language and selected descriptor
redaction. Do not compare only redacted descriptors to decide equality:
different underlying values can have the same redacted presentation. This file
owns no persistence, endpoint readback, grant authorization or resource mutation.

[test_graph_diff.py](../../../../../../control-plane-kit-core/tests/test_graph_diff.py)
covers empty/add/remove/modified forms, typed router edges, unsupported and
ambiguous transitions, custom spec preservation and raw/invalid graph rejection.
The [plan compiler](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py) is the next
interpreter and owns how structural changes become activities and review
blockers.
