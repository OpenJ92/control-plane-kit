Source: [control-plane-kit-core/tests/contract_security_assertions.py](../../../../control-plane-kit-core/tests/contract_security_assertions.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This unittest helper recursively rejects a specific closed set of raw-secret
field names in mappings/sequences, then searches the descriptor's rendered repr
for selected secret canaries. It is assertion support, not production redaction.

Do not infer universal absence of secrets, private addresses or arbitrary
credential encodings from a passing assertion. Unknown field names and values
outside the marker set are not classified by this helper, and it does not
replace each descriptor owner's public/protected-material policy. The test case
passed by the caller owns the failure; this helper never sanitizes a descriptor
for publication.
