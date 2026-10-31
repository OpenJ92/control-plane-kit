Source: [test_atomic_effect_attempt_fold_contract.py](../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py).

The existing exact import/call policy remains exact. The O2 correction follows
the reviewed lifecycle-first health path in `effect_attempt_fold_interpreter`:
one new `_lock_health_prefix` import/call, nonlocking request and attempt
locators, one additional `_fold` discrimination, one workspace denial, two
locator-change conflicts, and reuse through `health_prefix.require`.

Accordingly `_attempt_for_update` increases from three to four lexical calls,
`_fold` from two to three, `EffectAttemptFoldDenied` from 14 to 15 and
`EffectAttemptFoldConflict` from 31 to 33. `stores.execution.get_request`,
the qualified `_lock_health_prefix` and `health_prefix.require` each add one
call. The existing `_lock_effect_run_prefix` call moves but is not duplicated.
No predicate, expected policy finding or behavioral assertion is relaxed.

The source gate at `6c51f008` exposed the stale expectation; correction validation
is pending. These structural expectations supplement the actual lifecycle,
replay and refusal tests; they do not establish runtime behavior by themselves.
