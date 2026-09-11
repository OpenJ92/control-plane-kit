Source: [control-plane-kit-core/src/control_plane_kit_core/public_ingress.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/public_ingress.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Requested public exposure and reported endpoint values

An `IngressAuthorityReference` names an authority without carrying its provider
credentials. A `PublicIngressTarget` names a node and provider socket.
`NamedPublicIngress` combines those values with an ingress identity, connector
node, hostname, HTTPS exposure and ephemeral/retained/external lifecycle.
`PublicIngressRequest` is an alias, not a second representation. These values
neither register authority nor allocate a tunnel, DNS record or certificate.

Identity and node strings use a lowercase ASCII identifier grammar capped at
128 characters; socket names use their narrower grammar capped at 64. The
lowercased hostname must have at least two ASCII label-shaped components, with
no trailing dot; the original input has a 253-character ceiling. Validation
lowercases a temporary value, so accepted input retains its original text in
storage and descriptors. That original text need not be ASCII. It does not perform
DNS lookup, public-suffix validation or hostname ownership checks.

Construction admits typed target/reference values without resolving them.
The selected [graph codec](./topology/codec.py.md) checks referenced nodes/socket
and a shared target/connector runtime. The selected Operations
[origin helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/ingress_realization.py)
separately requires an HTTP endpoint with an internal `http://` URL. These are
different boundaries; the target value alone does not prove either condition.

`PublicIngressObservation` (also `ObservedPublicEndpoint`) records ingress ID,
hostname, URL, target, time, status and scalar evidence. `ready`, `unready` and
`unknown` are reported labels, not results of a probe performed by this owner.
The URL validator checks a literal `https://` prefix and bounded text; it does
not parse the URL, correlate its host with `hostname` or exclude all userinfo,
query, fragment or malformed-address cases. `observed_at` is nonempty text up to
512 characters, not a canonical timestamp or freshness check.

Evidence is copied into a plain dictionary with at most 32 nonempty keys of at
most 64 characters. String values are nonempty and capped at 512 characters;
integers, floats, Booleans and None are also admitted. There is no numeric range
or finite-number check, aggregate serialized-byte cap or JSON interoperability
guarantee. The frozen observation still exposes a mutable evidence dictionary.
Direct construction replaces any falsy evidence input with an empty mapping;
the decoder separately requires a mapping before construction.

All four codecs require their exact descriptor key sets, reconstruct typed
values and reject unknown closed enum values. This is structural decoding, not
canonical-byte encoding or provider admission. Descriptors retain public
addresses, names and evidence. The shared secret-marker filter is a finite text
heuristic, not arbitrary secret detection; unknown keys can be echoed by
descriptor errors and enum failures retain causes. Presentation and trusted
observation admission require their own policies.

Full 420-line owner and full 157-line
[test file](../../tests/test_public_ingress.py.md) read. Selected graph ingress
reference checks, Operations authority declaration and runtime/realization
helpers were inspected to locate ownership; this is not a full ingress-service
or provider audit. No executable validation, public exposure, credential use,
resource allocation or cleanup was performed.
