Source: [receiver_node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_node_control.py).
Maintain this companion with source and imported contract changes.

The V2 command request carries receiver target, authority context and declaration
identity alongside the existing variable, operation, request ID, idempotency key,
command codec, precondition and payload. READ_STATE has no command material;
APPLY_COMMAND requires matching typed payload/codec and precondition. The complete
canonical request has a nominal digest. Context changes do not change receiver
identity, but do change request identity.

The workload V2 grant adds the profile to the historical closed claim shape,
replacing the target and adding context/declaration. It has neither purpose nor
canonicalization fields because the historical workload grant has neither.
Its nominal digest is distinct from the request digest and historical grant.

Verification requires independent installed target/declaration and admitted
variable/operation plus issuer/key/audience/time. Well-formed discrepancies return
ordered codes: trust/time; workspace, runtime, node, socket, receiver; context;
declaration; variable; command; request. Each scope dimension compares local then
incoming claims before the next dimension. Valid but undeclared local variable
returns VARIABLE_MISMATCH. Malformed caller context raises a detached family
error; malformed incoming claims return GRANT_INVALID.

```python
decision = verify_workload_receiver_node_control_grant(
    authenticated_grant, request, expected_target=installed_target,
    expected_declaration=installed_declaration, expected_variable_name=admitted_variable,
    expected_operation=admitted_operation, expected_issuer=trusted_issuer,
    expected_key_id=trusted_key_id, expected_audience=installed_audience, now=now,
)
```

Frozen nominal values are reconstructed on encode and verification. Canonical
raw decoding bounds bytes before parsing, reuses the historical numeric-token
observer, and checks exact JCS equality. This preserves large finite float
round-trips while rejecting ambiguous spellings, duplicate keys and unsafe
integer versions/epochs. Existing payload decoders retain ownership of variable
semantics. Request cap16384, grant cap2111 and lifetime300 remain unchanged.

No signing, authentication, current-authority lookup, replay state, effects,
durable history or transport is provided. Congruent context is not current
permission. Historical languages remain distinct. Adopters await whole #1881.
