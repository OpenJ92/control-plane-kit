Source: [coordinator.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py).
Maintain this companion alongside its source.

The coordinator composes persisted execution intent, scheduling, effect-attempt
services, and existing lifecycle services. `_classify_current` is effectful: a
finished schedule can write run completion or failure. Runtime-management
admission therefore runs before classification at both context-load sites,
before legacy `STEP_STARTED`, intent construction, or attempt admission.

For guarded RUNNING material, prior uncertain or in-flight activity returns its
existing classification without redispatch. Authoritative terminal/paused states
retain ordinary classification. Newly executable unsupported material returns a
normal `UNSUPPORTED` result with no effect attempt or lifecycle transition.
The command's existing completion receipt records that result; exact same-key
replay returns it. A real interrupted or failed receipt write retains the existing
incomplete/UNCERTAIN semantics. No broad exception conversion hides a failure.

Only a supplied empty plan whose complete pinned graphs independently compile to
an empty plan can pass as a no-op. Caller-supplied emptiness cannot turn managed
deployment into recorded success. This adds no schema or provider mechanism and
does not reset, retry, reconcile, or delete historical effects. Existing actor,
lease, authorization, and transaction boundaries remain the owning services.
`test_execution_coordinator.py` covers these refusal/history/receipt laws with
event equality, zero adapter calls and forbidden attempt admission.
