Source: [node_health_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_health_reads.py).
Maintain this companion with source and imported contract changes.

# Exact workload health authority

A NodeHealthReadRequest binds workspace, graph revision, node, control socket,
runtime, declared liveness/readiness kind, V2 declaration identity and request
ID. The new request profile is workload-node-health-read-request.v1; SHA-256 of
its complete RFC8785 bytes provides a nominal NodeHealthReadRequestDigest.
There is no endpoint, mutable variable, command, opaque authorization-context
digest or Operations state in this language.

The grant uses workload-node-health-read-grant.v1 and the dedicated
WORKLOAD_NODE_HEALTH_READ purpose. Static surface, variable, native probe and
transit authority cannot substitute. Existing profiles and keys gain no new
admission. Core defines unsigned claims; SDK must check the exact health JWT
profile, key-purpose snapshot, signature and congruent outer/inner claims.

The pure verifier requires independent expected_target, expected_runtime_id,
expected_declaration and expected_kind, plus issuer/key/audience/time. Expected
local values must come from trusted composition, and kind from the actual
admitted route. A request reconstructed from signed claims proves consistency
only. SDK must compare exact GET raw path /__control/health/liveness or
/__control/health/readiness, reject query/body/duplicate bearer input, and
complete admission and exact handler coverage before invoking a callback.
Existing product health paths share underlying checks where semantics match;
SDK extends its one atomic installer. Core parses no HTTP and calls no handler.

Verification rejection precedence is: grant type, purpose, issuer, key,
audience, time; request versus local workspace/revision/node/socket, runtime,
declaration (including V2 and socket congruence), kind/declaration membership;
then grant versus request workspace/revision/node/socket, runtime, kind,
declaration, and request ID/digest. Malformed expected arguments produce a
categorical contract error. Rejection results expose no submitted values.

Epochs are strict integers in the RFC8785 safe range. issued_at <= not_before
< expires_at and expires_at - issued_at <= 300. Validity includes not_before
and excludes expires_at. This does not promise immediate revocation.

Request/grant maxima are 1083/2107 canonical bytes. They use the existing
128-character identifier, 256-character reference and 64-hex digest grammars;
readiness is the longer kind and valid 16-digit epochs attain the time bound.
Whole-envelope limits precede nested Mapping decoding. Mapping codecs do not
claim raw JSON duplicate detection; that belongs to the SDK parser. Repr and
failures hide request/JTI/issuer/key/audience values and do not retain causes.

Operations #1821 must allocate fresh ID/digest for each logical observation or
evidence-seeking retry, bind it to approved plan/run/effect attempt and prior
observation, and check admission before issuance/dispatch. Same-ID
retransmission remains the same observation; a late result cannot count under
a new request. Core does not mint IDs or own replay/retention/age decisions.

Kepler passed this pure design. The current backend schema and SecretUseIntent
do not yet admit the new signing purpose. #1821 and Interpreters #149 own the
explicit schema/issuer/material handoff, with Core/Secrets owner work as needed.
Do not alias old purposes or infer provisioning from this enum. No persistence,
crypto, gateway transit, SDK or provider implementation is included in #1826.
