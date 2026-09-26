Source: [wrapper_configuration.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/wrapper_configuration.py).
Maintain this companion with source and imported contract changes.

One shared `workload-node-control-configuration.v1` envelope reuses existing
target/runtime/declaration and public-key values. Its purpose-separated verifier
families are exact: surface-read always, workload command iff variables exist,
and workload health iff health reads exist. V1 variable-only and V2 health-bearing
declarations keep their existing identities and wire formats. This new envelope
does not reinterpret application-specific configuration profiles.

The codec accepts a closed document or bounded UTF-8 bytes, rejects duplicate
JSON keys and non-finite constants, and emits canonical JSON bytes. Configuration
is immutable; public keys are canonical, purpose-scoped, sorted and unique by ID
and fingerprint within each family. Existing Core key validation checks public
PEM shape, not cryptographic usability; the SDK retains that responsibility.
Errors are fixed and detached, without candidate contents or exception chains.

`CPK_WRAPPER_CONFIGURATION_FILE` is a protocol-owned public path binding. The
pure selector receives values, does not read an environment or file, and returns
the exact supplied JSON/0444 artifact whose decoded surface matches the single
declared management receiver. IDs and allowed paths are not product-specific.
Supply effective public and socket-derived assignments; duplicate, indirect,
missing or ambiguous bindings fail. This profile supports one wrapper management
receiver per wrapped application/node, with multiple variables or health reads;
it does not limit the number of applications in a runtime. Independent multiple
receivers are explicitly unsupported rather than first-match selected.

Operations owns the separate registered-versus-selected slot/provenance join,
current authority, history and unchanged revocation law. It must decode the
selected artifact rather than substitute descriptor default bytes. SDK owns
loading/file integrity, configuration defaults, real verification, callbacks and
lifecycle setup. CPK composition supplies deployment-specific identity/public
verification facts; this module creates no credentials, policy store or effects.

Example shape (existing deployment inputs, no user-authored key material):

```python
codec = WorkloadNodeControlConfigurationCodec()
content = codec.encode_bytes(configuration).decode("utf-8")
artifact = ConfigurationArtifact(
    artifact_id, path, ConfigurationMediaType.JSON, content,
    ConfigurationFileMode.READ_ONLY,
)
environment = (PublicStaticEnvironmentBinding(
    WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, path,
),)
```

The governing tests cover two independent products/paths through actual product
and graph codecs, closed/bounded wire, exact key-family requirements, canonical
facts and fail-closed artifact selection. They do not establish a running SDK
receiver, signature verification, image qualification or live readiness.
