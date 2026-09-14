Source: [control-plane-kit-core/tests/test_delegation_authority_projection.py](../../../../control-plane-kit-core/tests/test_delegation_authority_projection.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Six laws of verifier projection

The test-local block implementation returns inert materialized metadata. Its
Docker runtime and gateway names are topology values, not launched processes.
The two PEM fixtures contain simple textual bodies; they establish the public
key text contract, not usable Ed25519 keys or signature verification.

The tests cover authored binding survival through compilation/graph encoding;
separate one-key, overlap and replacement realized graph values without changing
the authored descriptor; key-order normalization; selected generated environment
values and key ordering; identical-projection materialization; and realized
graph codec round trip. Authored output is checked for absence of generated
verifier environment and a secret-reference substring in this fixture.

Negative cases reject a projection for a nonmatching binding identity, duplicate
key IDs, an untyped key and a decoded realized projection after removing its
authored bindings. The first of these fails exact binding-set coverage; it does
not independently exercise a matched binding whose node is missing.

A key-set/projection change produces one closed verifier-projection diff and a
`ReconcileNode` followed by `WaitForHealthy` plan. The diff retains key IDs but
omits PEM. This proves planned operation shape, not successful reconciliation,
installed verifier material or healthy provider state. The final case preserves
object identity for an ordinary graph with no bindings or projections.

Full 331-line file, including fixtures/helpers, and full
[delegation-authority owner](../src/control_plane_kit_core/delegation_authority.py.md)
read. Tests do not cover every reference/key bound, fingerprint tampering,
issuer mismatch, reserved-environment collision, oversized rendered key map,
multiple purposes on one node or every preexisting projection conflict. No
database, cryptography, credential issuance, provider or Docker validation was
executed for this companion.
