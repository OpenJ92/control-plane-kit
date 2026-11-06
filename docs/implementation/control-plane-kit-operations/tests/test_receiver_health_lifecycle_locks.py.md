Source: [test_receiver_health_lifecycle_locks.py](../../../../control-plane-kit-operations/tests/test_receiver_health_lifecycle_locks.py).
Maintain this companion alongside its source.

Ten target methods schedule actual PostgreSQL contention: lifecycle must precede request/run/attempt/session/workspace/runtime locks for standalone reload and nested fold; revocation and stale selection after the wait must be rechecked. Actual fold-produced prefixes must reject bare, foreign, ended-transaction or same-transaction changed truth without late lifecycle acquisition or history writes.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
