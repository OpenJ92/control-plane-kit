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

Configuration accounting remains one command-local ContextVar owner created by
the existing managed-execute or reobserve entrance. `_configure_run` may activate
that ledger for a plan containing configuration work. Immediately around awaited
managed health I/O the coordinator pauses the same ledger and restores its prior
active state in `finally`, including cancellation and error. It does not allocate
a second ledger or discard cumulative usage. ContextVar task isolation keeps unrelated command owners
separate; child work for the selected health await observes the intended inactive
state.

For a supported retained connector observation (#2009), fresh dispatch also joins
the accepted installation proof. The existing dispatch UoW still reloads and
locks the request/run/attempt, checks worker/fence, accepted plan, current runtime
registration and lease. It compares the native intent, original observation event
and plan with the captured realization, then requires workspace current pins to
equal the execution base. Core's existing management observation resolver selects
the runtime/connector relation; the accepted-source store remains the sole owner
of historical installation and generated-token proof.

Only that public proof read briefly activates the same run ledger, checking its
owner and execution context and restoring its prior state in `finally`. A complete
proof creates a new immutable `ActivityRealizationContext` with the optional,
repr-hidden `connector_installation` field. Strict validation binds its current
pins and connector identity to the observation. Its embedded request/receipt
identify original creation without transient grants; the current observation
keeps its own event/activity identity. Historical credentials do not replace or
constrain current runtime authority. Fresh bootstrap and signed health retain
`None`, and all provider awaits remain outside UoWs with inactive accounting.

Closed unavailable/capacity proof skips provider I/O and uses the existing guarded
`NativeConnectionReadFailure` fold. The original ledger's used footprint remains
charged. Exhaustion can therefore commit a durable UNCERTAIN attempt and then
cause `ExecutionCoordinatorConflict` at the existing post-fold context reload.
The active bounded-context error boundary includes its initial request/run
locator as well as material loading; lock order and ownership/fence checks are
unchanged. Both accounting modes share one lexical and executed lock call. Only
the active mode translates the existing bounded-data exception tuple; inactive
errors propagate unchanged. Coordinator errors and unexpected RuntimeError still propagate.
The response failure does not erase that outcome; exact command replay reports
uncertainty without a new proof read, attempt or effect. There is no direct-result
shortcut or budget reset. Unexpected proof-reader exceptions propagate unchanged
and retain STARTED, as do admission denials; replay may report uncertainty while
that durable attempt remains STARTED.

`test_native_connector_installation_dispatch.py` covers real A→B dispatch,
separate creation/observation identity, strict copied-witness negatives, scoped
durable-proof corruption, exhausted shared accounting, exceptional restoration,
pin drift, and explicit successors/replay. Existing current-connector installation
and managed-admission tests continue to own full proof and topology/material
validation. This source join does not activate a retained-container Interpreter.

For the `managed-update-v1` gateway `ReconcileNode` only, a provider success is
admitted only when Core reads a fully correlated installation receipt with
`REPLACED` disposition. Bare success, invocation-completion-only evidence,
malformed or mismatched evidence, and CREATED/REUSED dispositions become a
correlated UNCERTAIN result before the coordinator constructs its fold command.
Other profiles and managed activities retain their established result semantics.

The managed application targets exercise these paths through the actual
authenticated Operations application, PostgreSQL owners and recording effect
ports. They are source-composition evidence only; concrete server transport,
provider execution and the live grandparent capstone have separate owners.
Core declares `command.deployment.reobserve-connector` as an authenticated POST
command at `/workspaces/{workspace_id}/runs/{run_id}/reobserve-connector`, with
the `reobserve_connector_connection` MCP parity identity, required idempotency
and current approval. These are pure protocol values. Servers181 still owns
transport registration and the bounded request schema implementation.

## O2 / #1883 current boundary

The signed managed-health port now returns exact Core ReceiverHealthReadResult. The coordinator revalidates that nominal value and compares its complete request, including authority context, before projecting the existing bounded outcome. Legacy NodeHealthReadResult and malformed/foreign V2 results cannot fold success. Dispatch and fold retain their existing runtime authority checks; the coordinator neither verifies credentials nor introduces provider behavior.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.

## B1 / #1923 configuration preparation

Configuration commands open one logical evidence ledger before preliminary
reads. A bounded plan-pinned route selects existing-owner readers; fresh work
loads only both pinned graphs' products and runtime authorities, then the
selected activity's pull, delivery and ingress material. One event-free material
translator derives the same intent for the coordinator and first-start owner.

Original in-flight replay locates the retained intent before fresh translation.
Its narrow observation context carries no invented catalog material and hands
ExistingAttempt to reconciliation. Managed authority still precedes effectful
classification. The ledger joins nested start, reconciliation and fold within
the same task/thread and command identity; material caches remain UoW-local.
Read refusal does not authorize dispatch or manufacture current-use truth.

The observation-only replay context already carries the verified original
intent. Execution consumes it directly; unrelated legacy effect steps do not
open an extra intent-locator UoW. Request-before-run lock tests identify the
actual locking connection after the preceding read-only routing connection.

The private context loader requires the command's accounting scope when its
pinned graph contains configuration. Production callers enter through the
existing execute, managed-execute or reobserve roots. Direct-context test
fixtures explicitly establish the same accounting and routing precondition;
they join an existing same-command ledger and do not introduce a production
standalone fallback or reset an existing budget.

Selected ancillary material belongs to one activity. The synthetic retained-
history fixture therefore reloads each selected activity through the same
material owner and translator in one shared accounting scope and UoW. Its
canonical graph also registers its declared runtime through the real owner.
These are test premises, not configuration allocation, provider or health proof.

B2 constructs the selected Start/Reconcile configuration proposal while the
optimistic context UoW is open, before fingerprinting. The context retains only
the immutable intent. The first-start owner invokes the same selection again
with fresh evidence under the existing lifecycle lock, and refuses complete
intent/fingerprint drift. Original replay remains first and catalog-free;
unrelated operation translation keeps its existing execution path. This reuse
connection awaits concrete review and focused validation. Unsupported, failed,
uncertain and non-running contexts retain their existing classification before
proposal selection. The corrected regression pairs real uncertainty with
independently ready configuration work; after real failure Core blocks the
pending work, so that subcase protects disposition only. Both forbid fresh
proposal selection while checking public status and unchanged protection.
