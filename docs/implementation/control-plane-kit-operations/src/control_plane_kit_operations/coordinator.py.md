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

Fresh managed deployment has a separate awaited entrance,
`execute_managed(ExecuteManagedActivityRun(execution, trusted_context))`.
The authenticated context is retained separately from the worker fence in the
command receipt. Complete health-port selection and the canonical fresh-owned
graph-pair policy precede creation. A private generator shares the existing
mutation engine between synchronous and awaited execution; only health reads
yield to the awaited driver. The synchronous entrance continues to refuse fresh
managed health plans.

The native connection obligation completes each passive read separately.
CONNECTED succeeds only when fresh at database acceptance; DISCONNECTED,
UNKNOWN and stale samples retain their original evidence and leave the step
waiting without failure or compensation. Refused or interrupted reads remain
UNSUPPORTED or UNCERTAIN; they are not completed UNKNOWN observations.
Independent signed PATH/readiness work can progress while native-dependent
work waits.

`reobserve(ReobserveConnectorConnection(execution, trusted_context, predecessor))`
is the sole n+1 entrance. It requires the current completed NOT_READY predecessor
and excludes competing in-flight, failed or uncertain work. The receipt,
successor start event, immutable intent and attempt commit together under the
request lock, before exactly one external read. Distinct keys cannot consume
the same predecessor; an incomplete same-key receipt never dispatches again.
The realization context admits the observation-restart event only for the
native connection operation; mutation realization still rejects that event.
Replay precedes the latest-predecessor check and returns the retained result,
even after later reads have completed.

Original signed preparations are never extended or recreated. The driver can
await the remaining fraction of their first integral second, then reload current
authority before dispatch. Acceptance reuses the same signing-authority owner
inside the fold transaction to recheck original grants, current keys/references,
approval and lease. Native entry and acceptance compare the selected runtime
registration, and classify samples using database time after authority locking.
Cancellation inside a read retains uncertainty when current authority permits,
then propagates cancellation; interrupted admission remains an incomplete receipt.
No transaction spans a wait, signing, SDK call or network read.

The managed application targets exercise these paths through the actual
authenticated Operations application, PostgreSQL owners and recording effect
ports. They are source-composition evidence only; concrete server transport,
provider execution and the live grandparent capstone have separate owners.
Core declares `command.deployment.reobserve-connector` as an authenticated POST
command at `/workspaces/{workspace_id}/runs/{run_id}/reobserve-connector`, with
the `reobserve_connector_connection` MCP parity identity, required idempotency
and current approval. These are pure protocol values. Servers181 still owns
transport registration and the bounded request schema implementation.
