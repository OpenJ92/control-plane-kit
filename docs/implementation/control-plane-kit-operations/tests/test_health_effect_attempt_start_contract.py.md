Source: [test_health_effect_attempt_start_contract.py](../../../../control-plane-kit-operations/tests/test_health_effect_attempt_start_contract.py).

The exact constructor signature expectation adds only the reviewed keyword
`health_receiver_decoders`. All command, scope, nominal-value and pre-UoW
refusal assertions remain intact. Empty composition preserves construction and
invalid-command testing while refusing an otherwise eligible fresh health start.
