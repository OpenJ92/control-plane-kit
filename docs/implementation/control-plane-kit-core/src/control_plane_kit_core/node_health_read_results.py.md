Source: [node_health_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_health_read_results.py).
Maintain this companion with source and imported contract changes.

A NodeHealthReadResult contains an exact request, its matching V2 declaration,
and one closed outcome: healthy, unhealthy, unknown or unsupported. The result
codec also takes that trusted request/declaration context. It derives profile,
canonicalization, request ID/digest, kind and declaration identity from context;
only the outcome is selected from the incoming payload. Changing target,
runtime, kind, request ID or declaration prevents result substitution.

Healthy/unhealthy report the affirmative/negative condition observed by the
requested check. Unknown means the admitted check could not determine the
condition; unsupported means that known declared check cannot be performed in
its supported environment. Neither means healthy. Unknown/undeclared kinds,
missing handler coverage, auth failures, timeouts and malformed transport are
separate SDK failures; do not fabricate these as workload outcomes. Existing
static status remains variable registry coverage, not readiness.

The exact result profile is workload-node-health-read-result.v1. Its canonical
maximum is 446 bytes: ID128, two digests64, readiness9 and unsupported11 plus
fixed profile/canonicalization/JSON keys. A context-specific bound subtracts
unused request-ID/kind capacity before payload interpretation. No arbitrary
metadata, URLs, logs, exception strings, credentials or timestamps are allowed.

Repeated decoding under the same request is the same logical observation.
A result for an old request fails against a fresh request, but a digest is
correlation, not authentication or proof that a provider was observed recently.
Operations owns deadlines, current approval, retries and history; SDK/product
owners must invoke their check after admission and produce truthful evidence.
Core has no clock, I/O, signature checks, stores or callback registry.
