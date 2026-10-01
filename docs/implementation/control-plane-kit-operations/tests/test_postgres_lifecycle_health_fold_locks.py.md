Source: [test_postgres_lifecycle_health_fold_locks.py](../../../../control-plane-kit-operations/tests/test_postgres_lifecycle_health_fold_locks.py).
Maintain this companion alongside its source.

The existing fold schedules require the distinct latest run to be locked before
attempt/runtime rows and keep terminal replay independent of current authority.
The prepared-reload test now obtains the actual complete health prefix through
the lifecycle-first preparation entry, using a nonlocking request locator before
it. Initial reload must succeed. A same-UoW run-status mutation must then refuse
without discovering/locking a new latest run or writing rows, while the held run
snapshot remains RUNNING. Caller rollback preserves all original history.

This is the narrow O2 translation requested by Meridian's source HOLD. The
separate receiver-health target retains bare-prefix refusal. No production
relaxation, new authority or additional test matrix is introduced. Implementation
green remains pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
