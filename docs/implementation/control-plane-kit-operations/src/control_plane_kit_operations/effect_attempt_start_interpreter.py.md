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
