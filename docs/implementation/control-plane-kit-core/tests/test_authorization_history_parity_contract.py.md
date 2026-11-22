Source: [test_authorization_history_parity_contract.py](../../../../control-plane-kit-core/tests/test_authorization_history_parity_contract.py).
Maintain this companion alongside its source.

The exact security parity inventory includes the workload verifier read added by
#1877, increasing the expected operation count from 75 to 76. All existing
authentication, effect, history, disclosure and negative-case assertions remain
unchanged. The dedicated route/projection/parity contract tests identify the new
entry and preserve the complete existing inventory.
