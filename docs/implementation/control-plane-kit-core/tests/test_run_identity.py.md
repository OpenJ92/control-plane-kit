Source: [control-plane-kit-core/tests/test_run_identity.py](../../../../control-plane-kit-core/tests/test_run_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

The [RunId owner](../src/control_plane_kit_core/operations/run_identity.py.md)
must remain one exact nominal class across imports and descriptor consumers.
This file checks frozen/slotted values, raw-string inequality and canonical
grammar at the effect-attempt/request boundary. Journal fields and optional
secret-grant run fields deliberately remain strings; they must share admission
without silently accepting malformed or subclassed text. Optional grant run
identity permits None where its owning contract says so.

The import guard test ensures a missing nested dependency is not hidden as a
missing target module. Preserve it when changing test discovery helpers.
Import-order and private-helper witnesses support the
[shared grammar](../src/control_plane_kit_core/_run_identity.py.md); they do not
prove durable journal insertion or remote secret authorization. No suite was
executed while authoring this note.
