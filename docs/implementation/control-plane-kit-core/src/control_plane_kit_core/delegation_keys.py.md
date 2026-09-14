Source: [control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Public key identity before cryptographic interpretation

DelegationPublicKey combines a bounded key ID, the closed ED25519 algorithm
label and public PEM text. Four purpose values distinguish gateway probes,
workload node control, workload surface reads and gateway transit. A key value
does not itself carry a purpose or grant permission for one.

The constructor admits IDs beginning with a lowercase ASCII letter followed by
up to 127 lowercase letters, digits, dots, underscores or hyphens. PEM input is
limited to 8192 characters and ASCII. Normalization changes CRLF to LF, strips
outer whitespace and adds a trailing newline. It then requires public-key
boundary lines, a nonempty body with no empty lines, and no case-insensitive
PRIVATE substring.

These are text-shape checks. They do not parse base64, decode an asymmetric key,
verify Ed25519 material or prove possession. The fingerprint is SHA-256 of the
normalized ASCII PEM text, not decoded key bytes. Alternate textual formatting
can therefore have a distinct identity even if an external parser considers it
the same key.

The ordinary repr suppresses the PEM field, and descriptor returns only key ID,
algorithm and fingerprint. The normalized PEM remains accessible as a public
attribute for verifier configuration; omission from this descriptor is not
erasure or a universal serialization rule. Errors are not a general redaction
boundary, and the ASCII rejection retains its original exception cause.

[Focused tests](../../tests/test_delegation_keys.py.md) cover the identity
descriptor, a private-header rejection, closed purposes and distinct policy
scope labels. Cryptographic parsing, registration, custody, signing and
verification belong to consumers. This file performs no key generation, secret
resolution, clock, storage or provider action.
