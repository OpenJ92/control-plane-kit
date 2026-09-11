Source: [control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Name authority, connection material and process delivery separately

RuntimeAuthorityReference is a bounded lowercase identifier for authority
selected elsewhere. Its identifier grammar and rejection markers prevent
selected secret-shaped strings; they do not establish a registered authority,
permission, live endpoint or universal secret detection.

RemoteDockerTlsConnectionAdmission carries an exact authority reference and
three exact SecretReference values: CA certificate, client certificate and
client key. These are private connection references for the interpreter.
Its descriptor replaces the three references with redaction markers and repr
omits them. It is deliberately not a lossless storage codec. No TLS bytes are
read, parsed or connected here.

runtime_connection_secret_uses requires the expected authority to match and
returns the three reference/use-intent pairs. One reference may fill multiple
roles because each role has a distinct intent. None produces no uses.
validate_runtime_connection_grants checks exact tuple/grant types, unique
reference/intent pairs and membership in those allowed uses, plus exact
workspace/effect/run/activity coordinates. Expected correlation strings have
their own bounded grammar.

Empty and partial grant sets are structurally valid. This function neither
proves all connection material is present nor rechecks durable authorization,
revocation, credentials or every grant field. The interpreter must require
complete admitted uses before resolution/provider access. Mixed product, pull
and connection grants need validation of each domain, not silent filtering
until this narrower checker accepts them. The
[connection tests](../../tests/test_runtime_connection_admission.py.md) protect
this distinction explicitly.

RuntimeAuthorityAccessDelivery instead describes authority access to be
delivered to a process: local Docker socket mount, remote TLS secret files or
cloud credential secret session. Labels are bounded lowercase identifiers and
unique within a delivery. Secret-reference strings may be coerced to typed
SecretReference here, unlike exact typed connection admission. Local socket
delivery forbids secret references; remote/cloud declarations do not themselves
enforce complete required labels or resolve their material. Normalization
requires a typed tuple, unique authority references and canonical ordering.

Delivery descriptors retain opaque secret-reference handles; the private
connection descriptor hides them. Neither description supplies actual credential
bytes. The delivery codecs require exact descriptor keys and a list of nested
secret-reference descriptors, then invoke the value constructors. Decoding
normalizes ordering rather than demanding already-canonical input bytes.
No aggregate collection limit or universal exception sanitization is supplied:
unknown field names and chained construction errors can remain visible.

Whether a particular node may receive a declared delivery is checked by
[runtime request material](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
and [runtime intent](runtime_effect_observation.py.md), not this declaration
owner. Start/reconcile recipient rules, teardown retention and observer
connections have separate
[test witnesses](../../tests/test_runtime_authority_recipient.py.md).
No mount, credential resolution, durable admission, Docker connection, deployment
or retry is performed by this module.
