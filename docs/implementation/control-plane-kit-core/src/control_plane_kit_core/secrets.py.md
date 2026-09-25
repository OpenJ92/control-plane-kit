Source: [secrets.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py).
Maintain this companion alongside its source.

`SecretUseIntent` is the closed reason for an exact secret use. Existing grants,
custody and delivery descriptors remain reference-only; they contain no resolved
secret value. #1841 appends two distinct health signing intents while preserving
every old member, ordering and descriptor field set:

| Delegation key purpose | Secret-use intent |
| --- | --- |
| `WORKLOAD_NODE_HEALTH_READ` | `workload.node-health-read-signing-key` |
| `GATEWAY_NODE_HEALTH_READ_TRANSIT` | `gateway.node-health-read-transit-signing-key` |

The module-local public `health_signing_intent_for(purpose)` expresses only this
two-member correspondence. It checks nominal `DelegationKeyPurpose` identity
before comparing enum members; raw strings, other enums and non-health purposes
receive fixed `SecretProviderContractError` without candidate detail or a parser
cause/context. No generic issuer, lookup registry or fallback family is added.
The import from secrets to the independent delegation-key language is acyclic.

Existing resolution grants still permit only the exact reference and nominal
intent. Existing environment/file descriptors carry the new strings through the
same strict field sets. Their prior decoding/error behavior is unchanged; this
slice does not claim a broader decoder error-chain hardening. Tests in
`test_health_signing_intents.py` cover exact pairing, nominal/family refusal,
grant isolation and descriptor round-trips/rejection. The established exact
intent inventory preserves all old entries and adds the two reviewed values.

This is pure correspondence, not key possession, issuer admission, provider
acceptance or permission to sign. Operations #1842 owns fresh-store purpose/use
adoption, Secrets #29 owns its provider allowlist and family/provisioning contract,
and #1846/Interpreters #149 own approved-attempt reload and immediate-use material.
Those consumers must adopt the exact families explicitly. No key generation,
signing, network, schema, durable history or retained-store change occurs here.
