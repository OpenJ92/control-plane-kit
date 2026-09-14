Source: [control-plane-kit-core/tests/test_external_product_fixture.py](../../../../control-plane-kit-core/tests/test_external_product_fixture.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file sends an external
[descriptor-only fixture](../../../../control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json)
through product decoding, catalogue instantiation, topology compilation, graph
round-trip, validation, diff and initial activity planning. It protects retained
product identity, descriptor digest and OCI reference without importing product
implementation code.

The fixture has a synthetic repeated-character image digest. The test proves
that a typed StartNode plan can be produced and declared ready at the pure
boundary; it does not pull the image, prove it exists, grant approval or deploy a
proxy. Keep those different meanings of “ready” explicit.
