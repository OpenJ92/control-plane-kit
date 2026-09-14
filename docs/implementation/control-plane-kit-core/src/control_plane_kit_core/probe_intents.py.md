Source: [control-plane-kit-core/src/control_plane_kit_core/probe_intents.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Describe a check without performing it

The four intent variants separate process, transport, application health and
readiness. ProbeSubject supplies graph-declared endpoint identities and an
optional health path; RuntimeEndpointObservation supplies a correlated endpoint
value from an interpreter. Endpoint context distinguishes runtime-private,
host-local and public meaning. A context label is not network authorization,
and a constructed observation is not proof a provider produced it.

process_probe constructs a process intent. Transport and health factories
compare subject ID, declared socket and protocol against the supplied endpoint.
Selected mismatches return ProbeConstructionFailure values. Health additionally
requires HTTP and a declared health path. These factories use the endpoint's
graph ID; they do not independently compare it with fresh current graph truth.
Malformed inputs and constructors can raise exceptions rather than returning a
failure value. The declared failure vocabulary is broader than the factory
branches; unsafe health paths are rejected during value construction.

Runtime endpoint identity strings must be exact strings, nonblank, at most 512
characters and free of characters below code point 32. Address variants are
exactly LiteralEndpointMaterial or SecretEndpointMaterial. Each address text
is bounded at 512 characters, and the encoded endpoint evidence wrapper is
bounded at 4096 bytes. Literal material alone has only text checks; embedding
it in an endpoint checks URL scheme compatibility, hostname and explicit port,
and rejects user information, query and fragment. Ordinary paths are allowed.
This is no DNS, reachability, egress, address-ownership or SSRF policy.

Secret endpoint material checks a secret:// prefix and size, not the complete
registration or resolution contract. Endpoint descriptors retain literal
addresses or opaque reference IDs exactly; they are not public redaction.
Probe intent descriptors include that endpoint material. Do not substitute
these values for a deliberately bounded public read projection.

ProbePolicy bounds attempts to 1–100 and response bytes to 1–65536, with actual
integer checks. HTTP expectations require integer status codes in 100–599 and
normalize them to a sorted unique tuple. TimeoutPolicy rejects booleans,
nonpositive numbers, values above 300 seconds and intervals above the total.
It lacks an explicit finite-number check, so NaN escapes the range comparisons;
the test named for finite policy does not establish that case. This is a
source-derived limitation, not an executed reproducer or observed runtime
failure.

Readiness requirements are nonempty concrete probe kinds, deduplicated in
declared order; this file does not aggregate observations into readiness.
ProbeObservation checks the kind/outcome relation, attempts and endpoint
context: process observations cannot have one, transport/health require one,
while readiness does not prohibit one here. Downstream record owners may have
stricter rules. No observation carries a timestamp or proves freshness.

Direct intent constructors check their substantive fields but do not validate
that the caller-supplied kind field matches the variant's default. Factory
defaults preserve the intended names; arbitrary constructed intent values need
consumer validation. Subject/probe identity and health-path checks are less
bounded than endpoint evidence checks. Error causes and ordinary dataclass
representations are not universal redaction boundaries.

The [focused tests](../../tests/test_probe_intents.py.md) protect these distinct
layers and selected rejection rules. [Protocol](types.py.md) owns admissible
scheme vocabulary. [Runtime-effect observation](runtime_effect_observation.py.md)
adds its own result-level meaning. This module opens no sockets, resolves no
secrets, inspects no runtime and grants no retry or mutation authority.
