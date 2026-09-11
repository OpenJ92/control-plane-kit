Source: [control-plane-kit-core/tests/test_identity.py](../../../../control-plane-kit-core/tests/test_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects [identity.py](../src/control_plane_kit_core/identity.py.md)
using explicitly constructed principals: credential-free descriptor shape,
closed unique workspace scopes, duplicate workspace rejection, denied context
scope changes and distinct operator/worker identities.

The test named “public payload cannot construct authenticated authority” rejects
passing an unrelated payload shape as constructor keywords; it does not prove
that every caller has authenticated its supplied principal. Similarly, the
“bounded descriptor” name checks a fixed shape and sample content, not arbitrary
identity-string length bounds. Preserve those evidence limits when describing
security. Actual credential verification and HTTP/MCP trust composition need
their owning tests.
