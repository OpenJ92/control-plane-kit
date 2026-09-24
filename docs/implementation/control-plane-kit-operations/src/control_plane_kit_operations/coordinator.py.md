Source: [coordinator.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py).
Maintain this companion alongside its source.

The coordinator composes persisted execution intent, scheduling, effect-attempt
services, and existing lifecycle services. `_classify_current` is effectful: a
finished schedule can write run completion or failure. Runtime-management
admission therefore runs before classification at both context-load sites,
before legacy `STEP_STARTED`, intent construction, or attempt admission.
The policy receives the existing context's product registrations and checks
both graph reference sets by exact identity and digest. Omitted authored fields
cannot hide the selected implementation's declarations; unrelated registrations
cannot trigger refusal. Malformed reference refusal is bounded and does not
strand the command receipt or permit a forged no-op completion.

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

The coordinator also passes the actual stored derivation profile to the shared
policy. A complete canonical managed-current to empty-desired teardown can then
use the existing structural effect paths. Support is independent of the existing
saved destructive approval, runtime/ingress authority admission and worker fence.
The real ingress adapter additionally requires secret-provider custody authority.
Runtime attempts/folds and ingress STEP/resource history remain distinct owners;
uncertain ingress removal stops progress and replay never redispatches it.
Effects stay outside transactions. Successful execution does not itself advance
current graph: the separate advancement service still checks complete successful
history. The PostgreSQL target composes these actual services with recording
provider boundaries and verifies current remains unchanged before advancement.
