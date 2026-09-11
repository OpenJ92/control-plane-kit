Source: [control-plane-kit-core/src/control_plane_kit_core/verification.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Semantic verification values

This file owns the closed check language and bounded result shapes; it does not
perform health probes. Checks name provider sockets and protocol-specific
operations. HTTP uses a leading-slash path without URL authority/query/fragment;
Postgres exposes SELECT_ONE with optional reference-based authentication rather
than an arbitrary query field. Broker/object-storage/SMTP variants are declared
checks, not proof that every interpreter implements them or that executing them
is side-effect-free.

[Protocol](../../../../../control-plane-kit-core/src/control_plane_kit_core/types.py) supplies expected socket semantics,
and [SecretReference](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py) carries Postgres password
identity without its value. The interpreter owns connection resolution,
authorization and actual execution. Constructing an authentication value does
not resolve a password or authorize its use.

VerificationPolicy declares timing, attempts and evidence bounds. Canonical
descriptors include interval_seconds; the supported legacy shape omits it and
decodes to 1 second. A source limitation remains: timing validation uses range
comparisons without an explicit finite-number check, so do not describe direct
policy construction as proving finite timing for every numeric input. This is
an unresolved admission concern recorded under
[#1801](https://github.com/OpenJ92/control-plane-kit/issues/1801), not permission
to change source within the documentation rollout.

HttpCheck's optional expected_body_sha256 is retained intent material. HTTP
completion evidence stores status, response byte count, expected digest and a
match boolean; it does not store response bytes or an observed-body digest.
Paired optional digest/match fields must agree in presence. The result
constructor checks typed shape, not whether a provider actually performed the
check or whether all supplied outcome/capability/evidence meanings agree.
[runtime_effects.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py) and its
fingerprint validator separately reject a successful effect carrying failed
verification.

[test_verification_capabilities.py](../../../../../control-plane-kit-core/tests/test_verification_capabilities.py)
protects check/codec closure, policy compatibility, typed evidence and graph
propagation. These are existing shape laws, not fresh network or health evidence.
Bounded fields and reference-only secrets also do not imply a fully redacted
descriptor: check paths, database/user names and references are retained.
