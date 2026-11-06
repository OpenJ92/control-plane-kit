Source: [test_delegation_signing_keys.py](../../../../control-plane-kit-operations/tests/test_delegation_signing_keys.py).
Maintain this companion alongside its source.

#1815 adds real PostgreSQL registration/activation and exact replay/readback for
both HTTP-sample purposes, with foreign-workspace absence and refusal when only
the opposite sample, V2 or probe use is admitted. Existing lifecycle and scope
proofs remain. These tests admit protected references and public fixture keys;
they resolve no private material and prove neither provider-version pinning nor
reached-check, cryptographic or external receiver behavior.
