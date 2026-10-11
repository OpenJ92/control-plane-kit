Source: [test_receiver_configuration.py](../../../../control-plane-kit-core/tests/test_receiver_configuration.py).

The existing tests protect strict V2 configuration codecs, purpose/key families,
identity and surface agreement, bounded categorical refusals, profile isolation,
and exact artifact binding across independent product paths.

Issue #1968 adds a public structural declared-slot selection law: an inert payload
needs no receiver identity or verifier values, but the same payload must still
fail actual configuration selection. Missing, duplicate, redirected or derived
bindings, ambiguous surfaces, wrong media/mode and forged paths are refused.
The test obtains the new public function at runtime and explicitly asserts its
absence, so intended red is a missing-contract assertion rather than failed
test collection. Root export equality is checked. Existing actual selection
tests are unchanged. This checkpoint is tests-first and unvalidated.
