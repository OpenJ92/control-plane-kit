Source: [control-plane-kit-core/tests/test_runtime_connection_admission.py](../../../../control-plane-kit-core/tests/test_runtime_connection_admission.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Connection grants are independent typed inputs

The [connection language](../src/control_plane_kit_core/runtime_authority.py.md)
tests check root exports, frozen fields, exact three-role use derivation,
typed slots and authority matching. One reference may serve different TLS
roles. Descriptor and repr assertions hide the connection reference handles.

Grant vectors explicitly accept empty, partial, complete, fresh and reversed
collections. Negative vectors reject duplicate uses even with distinct
authorization IDs, wrong references/intents, mismatched or malformed correlation,
untyped carriers and non-tuple grant collections. Selected rejections must have
bounded fixed text with no cause/context or private fixture values.

Full 243-line file and full authority owner read, with the real SecretReference/
SecretResolutionGrant constructors and imported grant fixture checked.
These tests deliberately do not prove grant completeness, durable issuance,
revocation checks, credential resolution or a successful TLS connection.
The structural checker cannot replace interpreter admission.
