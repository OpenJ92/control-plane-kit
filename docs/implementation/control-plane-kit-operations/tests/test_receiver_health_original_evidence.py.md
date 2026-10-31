Source: [test_receiver_health_original_evidence.py](../../../../control-plane-kit-operations/tests/test_receiver_health_original_evidence.py).
Maintain this companion alongside its source.

Five target methods require exact original V2 binding/configuration/context and gateway provenance. Corrupt original bytes retain their original witnesses and must refuse without repair; a separate documented Core decoder refusal on coherent bytes demonstrates codec reachability. Unexpected adapter failures remain distinct from bounded data refusal.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
