Source: [receiver_health_preparation_fixture.py](../../../../control-plane-kit-operations/tests/receiver_health_preparation_fixture.py).
Maintain this companion alongside its source.

Pure Core receiver values construct the proposed Operations V2 preparation. The fixture does not invent a fallback when the Operations record is absent. It keeps gateway own-control identity separate from workload identity and transit transport.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
