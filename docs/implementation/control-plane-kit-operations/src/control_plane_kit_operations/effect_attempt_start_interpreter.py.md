Source: [effect_attempt_start_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py).
Maintain this companion alongside its source.

`EffectAttemptStartService` owns one first-start transaction. The existing
`execute(StartEffectAttempt)` entrance remains unchanged. The constructor now
accepts the keyword-only trusted `health_receiver_decoders` composition, whose
empty default refuses fresh health authority while preserving replay. The additive
`execute_health(StartHealthEffectAttempt)` requires node-control read/execute,
delegation-key use and secret-provider use in the trusted context, independently
of worker `execution:operate` and the exact lease fence.

Both entrances use a single private body with a closed optional health companion.
The body retains request, request-scoped run and attempt lock order, current
claim/fence checks, original intent validation, and existing latest-run,
scheduling, phase and source checks. There is no nested execution call or generic
callback/transaction extension. Nonhealth behavior preserves the old law suite.

An absent generic health start refuses before clock/IDs/writes. Dedicated fresh
health admission checks approval, graph pair, active key pair, selected receiver
trust coverage and both absent
correlations before the existing database observation. The original event ID is
allocated first, followed by logical health request and both JTIs. The write
order is event → protected intent → attempt → transit use → workload use →
preparation → one commit request. All six facts are part of the same transaction.

An existing health attempt must have exact retained preparation, even through
the generic observation entrance. Replay needs current matching claim authority
but does not sample time or reissue key/reference authority. It never converts
an evolved attempt into a new start or repairs missing evidence.

The unit of work's `commit()` requests commit; the driver's commit happens on
exit. Precommit faults with successful rollback restore the prestate. An error
after actual commit can leave all six facts durable with an unknown caller
outcome. No successful result escapes a failed exit, and missing acknowledgement
is not proof of rollback. Exact replay may observe the retained result without
redispatch. #1846 must independently reload postcommit eligibility, including
not-yet-valid or expired intervals, before signing or external effects.

Operations1872 changes the shared coverage owner, not this transaction algorithm. health_receiver_decoders now carries gateway transit only; all workload/gateway own-health uses the registered and actually selected common Core configuration with an exact slot join. New conforming workloads require no decoder entry. Existing first-start ordering, ID/write atomicity, replay, clock, owner exception and uncertain-commit behavior remain unchanged. Empty transit composition still cannot authorize a fresh signed health operation.

C3 also requires the fresh receiver lifecycle prefix and original material
permission before and after writes. It compares the actual retained event,
intent and native attempt before commit, retaining that native expectation
before health wraps the returned value. An absent pre-lock locator that becomes
present after locking restarts once outside the UoW, before IDs/writes, to follow
ordinary exact replay. No arbitrary error or ambiguous mutation is retried.

Health-specific read/check admission follows the held session/workspace prefix
and precedes generic receiver semantic checks, preserving its selected-slot
refusal contract. Its correlation locks do not introduce a late earlier lock.
All receiver permission checks still precede lease observation, IDs and writes,
and the final retained-record/receiver rereads remain mandatory.

## O2 / #1883 current boundary

Fresh health work now runs the existing O1 receiver execution/material checks before health key selection and secret-use correlation. The lifecycle-first transaction order is retained. Dedicated and generic exact replay remain observational and bypass fresh receiver permission; pure V2 original-byte reconstruction is allowed, while V1 history retains its no-common-decoder law.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.

B1 / #1923 prepares configuration births only after fresh lifecycle permission.
The existing owner rederives the complete pinned material and source-domain
capacity before clock, IDs or writes. A private connection/identity/intent/guard
value gates all five durable components: event, intent, attempt, refs and claims.
Postinsert and replay validation require complete original protection. Existing
attempts retain their exact intent and claims without reminting or current
catalog selection; unsupported configuration compensation and B2 reuse refuse.
