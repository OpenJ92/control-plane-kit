Source: [control-plane-kit-core/tests/test_control_contracts.py](../../../../control-plane-kit-core/tests/test_control_contracts.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Validation and projection examples

The [control-contract owner](../src/control_plane_kit_core/control_contracts.py.md)
is exercised directly with explicit mappings. Variable tests check declaration
descriptors, required/optional handling, selected HTTP/TCP/Postgres forms,
plain value inclusion, secret presence markers and a runtime-map example.
Successful protocol-shaped strings do not establish connectivity or credential
safety.

Contract tests cover canonical-name and metadata-env lookup, refusal to read
process state, missing required values and an immutable patch rejection.
Snapshot descriptors must hide all values by default; unsafe projection shows
the non-secret URL but continues hiding SECRET. A runtime contract still loads
explicit supplied values and exposes a redacted descriptor.

Full 241-line test file read with the full implementation. The suite does not
exhaust successful patch behavior, unknown-key errors, canonical-name/env-alias
precedence, direct snapshot construction, shallow nested mutation, all byte
bounds or every URL/TCP edge. Repr and declaration metadata are not included
in its descriptor-redaction guarantee. No environment is read and no reload,
provider call, transaction or authenticated control action is exercised.
