Source: [control-plane-kit-core/tests/test_public_ingress.py](../../../../control-plane-kit-core/tests/test_public_ingress.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Six public-ingress value tests

The tests construct a named ingress targeting a node/socket and check its full
descriptor, HTTPS/ephemeral defaults, round trip and request alias. Sample
Cloudflare-flavored identifiers are fixture text: the value has no provider-kind
field and the test invokes no provider.

Authority-reference cases round-trip one valid identifier and reject selected
empty, uppercase, slash and credential-marker strings. Target cases preserve
the node/socket descriptor and reject an address-shaped socket name. Named
request decoding rejects three extra provider/credential fields, unsupported
TCP exposure and a hostname containing a path/credential marker.

The observation example reports ready with an HTTPS URL, text timestamp and two
numeric evidence fields. Assertions cover status, target, descriptor round trip,
the observation alias and absence of a token word in that fixture. One negative
observation uses a tunnel-token-shaped evidence value. These are selected
fixtures, not proof that every secret-shaped input is rejected.

The file does not test all size boundaries, hostname case preservation,
URL/hostname agreement, timestamp chronology, nonfinite numeric evidence,
post-construction evidence mutation or every malformed descriptor. It also
does not validate graph membership, authority registration, DNS, TLS, endpoint
health or lifecycle cleanup. Full 157-line file and full
[public-ingress owner](../src/control_plane_kit_core/public_ingress.py.md) read;
no tests or runtime operations were executed for this companion.
