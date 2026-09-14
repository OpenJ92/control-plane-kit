Source: [control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json](../../../../../../../control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This authored JSON fixture describes a container-server product
with an HTTP provider socket and no credentials, explicit ports or verification
checks. Its owned-ephemeral lifecycle and synthetic repeated-character OCI
digest are test material, not a published deployment recommendation.

[test_external_product_fixture.py](../../../../../../../control-plane-kit-core/tests/test_external_product_fixture.py)
uses it to prove descriptor-only product selection and pure graph/plan
propagation without importing external product code. Changing descriptor
material may change its digest and the law's expectations; the product codec
owns canonicalization, so do not infer identity from raw file formatting.
The fixture is included in companion coverage because it owns
behavioral input; it is not excluded as generated data.
