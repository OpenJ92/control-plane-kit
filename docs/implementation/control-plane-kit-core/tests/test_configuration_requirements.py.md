Source: [test_configuration_requirements.py](../../../../control-plane-kit-core/tests/test_configuration_requirements.py).

The #2011 tests exercise the public slot value/codec, canonical union and retained
path laws, exact concrete instance composition, explicit document variant,
unresolved material guard, declared receiver selection and declaration carry.
Real A/B payload content and digests differ while registered identity/image stay
fixed. The checked-in historical descriptor is read unchanged.

Receiver and carry fixtures reuse existing pure builders; no provider, current
registration or Operations execution semantics are simulated. Missing wire
preconditions are asserted before carry/material refusal claims. Existing tests
remain unchanged; all executable validation uses the owning Core Docker suite.
