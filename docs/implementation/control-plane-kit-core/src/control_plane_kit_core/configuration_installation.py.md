Source: [configuration_installation.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration_installation.py).
Maintain this companion with the source and imported contracts.

`ConfigurationInstallationReceipt` is a frozen assertion with five constructor
fields and the fixed descriptor profile `configuration-installation-receipt.v1`:
request fingerprint, full-selection fingerprint, disposition, nullable prior
realization fingerprint and current realization fingerprint. Every fingerprint
is an exact lower-case 64-character hexadecimal string. The exact enum
`ConfigurationInstallationDisposition` has `CREATED`, `REUSED`, `REPLACED`.
Created requires no prior, reused requires equal prior/current, and replaced
requires two distinct commitments. These laws constrain the assertion, not the
physical resource. Reprs omit receipt data.

`ConfigurationInstallationReceiptCodec` encodes/decodes exact mappings and
RFC8785 canonical bytes. `decode_canonical_bytes` permits only exact canonical
UTF-8 bytes up to 1,024 bytes. Parsing followed by exact re-encoding equality
rejects duplicate keys, whitespace, key order and escape aliases. Constructors,
descriptors and encoders revalidate values; malformed input produces bounded
`RuntimeEffectContractError`, with parser contexts detached.

`configuration_installation_receipt_for_result(context, result)` consumes the
existing `ConfigurationInvocationCorrelation`. It first validates that complete
context and the entire `RuntimeEffectResult`, including the existing 8,192-byte
canonical result ceiling. An absent `configuration_installation_receipt` entry
returns `None`. A present entry must decode, appear on SUCCEEDED, and match the
outer effect ID, original request commitment and full-selection commitment.
Neither result data nor fingerprints are rewritten. The reader interprets only
its own evidence key; invocation completion retains its separate semantics.

Example at a trusted provider's already-verified success boundary:

```python
context = configuration_invocation_correlation_for_request(request)
receipt = ConfigurationInstallationReceipt(
    request_fingerprint=context.request_fingerprint,
    selection_fingerprint=configuration_invocation_selection_fingerprint(context.selection),
    disposition=ConfigurationInstallationDisposition.REPLACED,
    prior_realization_fingerprint=prior_incarnation_commitment,
    current_realization_fingerprint=current_incarnation_commitment,
)
result = RuntimeEffectResult.succeeded(request.effect_id, evidence={
    "configuration_installation_receipt": receipt.descriptor(),
})
assert configuration_installation_receipt_for_result(context, result) == receipt
```

The example presupposes the provider's actual postcondition and provider-derived
commitments; constructing it proves neither. The provider must use stable,
domain-separated commitments to immutable resource incarnations, unchanged by
plan, request, graph revision or desired configuration. It must verify all
selected material, targets, digests, modes and workload-nonwritable delivery;
replacement additionally requires verified removal of the selected prior.
Failure, ambiguity or later client-close failure must not preserve this receipt.
#186 owns those effects/tests. #1911 owns original-source provenance, durable
admission/fold, attempt/fence, managed replacement policy and replay. The receipt
is not a signature, authority, health check, allocation cleanup proof, recovery
permission or protection against external-administrator races.

Imports reuse configuration invocation correlation and runtime result validation;
no provider, Operations, I/O, new dependency or result envelope is added. Existing
unprofiled history and failed invocation-completion semantics are unchanged.
