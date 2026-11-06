Source: [test_postgres_health_effect_start_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_health_effect_start_first_replay.py).
Maintain this companion alongside its source.

Live first-start/replay laws now assert receiver V2 target and exact authority_context. The unchanged atomic event/attempt/two-use/preparation and replay assertions remain. The former fresh BASE equal-content premise is explicitly moved to PostgresHealthHistoricalSideTests with the original V1 historical fixture: both equal-content projections remain separately identified, and the persisted BASE record is read back exactly. This does not claim fresh BASE management admission.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
