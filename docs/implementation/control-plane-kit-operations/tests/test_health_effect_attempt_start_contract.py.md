Source: [test_health_effect_attempt_start_contract.py](../../../../control-plane-kit-operations/tests/test_health_effect_attempt_start_contract.py).

The exact constructor signature expectation adds only the reviewed keyword
`health_receiver_decoders`. All command, scope, nominal-value and pre-UoW
refusal assertions remain intact. Empty composition preserves construction and
invalid-command testing while refusing an otherwise eligible fresh health start.

Native run35107369385 also exposed the admission module's stale explicit import
allowlist. Adding only `_health_receiver_trust` admits its reviewed Operations
selection/coverage helper. The full forbidden effect-call set, module traversal
and all other assertions remain unchanged. This is an exact dependency update;
it does not broaden the provider/signing/dispatch/clock boundary.
