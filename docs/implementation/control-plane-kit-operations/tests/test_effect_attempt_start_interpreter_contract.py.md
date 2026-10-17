Source: [test_effect_attempt_start_interpreter_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_start_interpreter_contract.py).

The generic first-start contract also asserts the exact service constructor
signature. It gains only `health_receiver_decoders`, matching the two health
contract assertions and the reviewed additive composition keyword. All generic
execution, scope, transaction and public-entrance assertions are preserved.
This third signature expectation was identified during static fixture migration.

Native run35107369385 exposed the remaining exact inventory expectation:
`health_receiver_trust` was absent from the interpreter dependency set. The
correction adds only that reviewed Operations contract name, retaining exact
set equality and every behavioral assertion. This failed gate did not reach
Operations compilation/clean import; a corrected native gate is still required.
