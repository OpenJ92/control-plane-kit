Source: [control-plane-kit-core/tests/test_gateway_delegation.py](../../../../control-plane-kit-core/tests/test_gateway_delegation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Twelve gateway-delegation language tests

The tests fix the two access-path labels and reject a provider name as one;
round-trip a complete HTTP request and show that changing path, target or kind
changes its digest; reject a Postgres path, an absolute origin URL used as a
path and an extra request URL field; and round-trip an unsigned grant's full
descriptor. They do not compute signatures or contact either target type.

Grant cases reject equal issue/expiry times, a 301-second lifetime, a malformed
digest, empty identity fields and selected malformed/oversized audience values.
Another case deliberately accepts benign public identifiers containing words
such as token, credential and secret. Extra compact-token/credential/signature/
authorization fields are rejected by the exact-key codec. The HTTP path case
rejects a literal token assignment; this is not an exhaustive URL or redaction
test.

The final cases inspect the canonical health-disclosure policy, the small
allow/reject result descriptors and an accepted-result rejection-code conflict.
Provider/transport neutrality is checked by searching four type names for six
terms. That name check is not a complete import, dependency or side-effect audit.

Full 342-line file, including grant fixture helpers, and full
[gateway-delegation owner](../src/control_plane_kit_core/gateway_delegation.py.md)
read, with selected actual target admission and issuing-service construction.
The fixtures derive a matching digest, but do not test every malformed claim,
unbounded absolute epoch, direct mismatched digest, path normalization variant,
alternate policy or result combination. Expiry at a real clock, grant replay,
signature trust, receiver target resolution and actual health disclosure require
their owning consumers' evidence. No executable tests, credentials, provider
operations or clock-dependent verification were run for this documentation.
