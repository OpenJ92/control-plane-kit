Source: [control-plane-kit-core/tests/test_delegation_keys.py](../../../../control-plane-kit-core/tests/test_delegation_keys.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Identity and vocabulary checks

A fixed public-PEM fixture exercises the
[DelegationPublicKey](../src/control_plane_kit_core/delegation_keys.py.md)
constructor and checks a 64-character lowercase hexadecimal fingerprint,
descriptor/fingerprint agreement and omission of public_key_pem. The test does
not independently compute a known digest, parse key bytes or sign a message.

A private-key header fixture must be rejected. Purpose enumeration is checked
exactly and an unknown value fails. Policy tests compare generation,
registration and use scope labels; unequal labels do not prove enforcement at
an authenticated boundary.

Full 80-line test file read with the full owner. Newline normalization, size
and identifier boundaries, malformed public PEM bodies, cryptographic validity
and repr behavior are not directly tested here. No keys are generated and no
custody, provider or live verifier is exercised.
