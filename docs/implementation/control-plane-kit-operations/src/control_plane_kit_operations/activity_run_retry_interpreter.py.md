Source: [activity_run_retry_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py).
Maintain this companion alongside its source.

Failed-run retry keeps its existing approval and journal owner, original request/fence, new-run linkage and replay semantics. It checks receiver coverage/pins/material separately before writes and after all writes. The final approval reread uses the same recovery owner as the initial check. Any late association/approval/material change rolls the transaction back; no external I/O occurs in the UoW.

No schema, provider, public token or caller bypass is introduced. These changes
remain unvalidated until the released owning Operations Docker gate passes.
