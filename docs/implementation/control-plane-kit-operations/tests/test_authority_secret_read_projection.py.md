Source: [control-plane-kit-operations/tests/test_authority_secret_read_projection.py](../../../../control-plane-kit-operations/tests/test_authority_secret_read_projection.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

Trace fakes exercise the five authority/secret families with only the relevant store configured. Page identity and workspace-before-store ordering are checked; detail calls retain their distinct lookup forms. Descriptor-based authority fixtures demonstrate selected key redaction, while real registered secret values must retain their existing descriptor exactly, including opaque references. This does not test provider material or broad secret detection.

Missing-store and missing-record cases preserve exact error categories and, where wrapped, original cause/context. Malformed descriptor/type cases are distinct from unexpected descriptor exceptions, whose identity remains observable. Assertions deliberately protect these existing seams rather than claiming all failures are sanitized.

The structural class records the existing extraction's ten-method partition, facade delegation/store ownership and selected import-direction checks. This is historical package-ownership evidence, not a requirement for a new AST framework or proof of every dynamic dependency. Shared import helper coverage lives in [test_read_services_package.py](../../../../control-plane-kit-operations/tests/test_read_services_package.py).

Full test source read for this companion. No database, authentication, real pagination SQL, provider authority or runtime behavior is demonstrated by these fake-store laws. See the [owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/authority_secrets.py) and its actual store contracts before extending semantics.
