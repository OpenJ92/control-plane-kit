Source: [test_health_signing_intents.py](../../../../control-plane-kit-core/tests/test_health_signing_intents.py).
Maintain this companion alongside its source.

The owner tests preserve the V2 purpose mapping and its nominal refusals. #1815
adds exact workload/transit HTTP-sample correspondence, refusal of other/raw
purposes, and exact reference/use grant isolation including cross-family denial.
These are pure value tests: they prove no key possession, current authorization,
provider-version selection, cryptographic signing or external behavior.
