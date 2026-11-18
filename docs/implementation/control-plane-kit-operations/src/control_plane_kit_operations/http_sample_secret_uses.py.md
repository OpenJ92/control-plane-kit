Source: [http_sample_secret_uses.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/http_sample_secret_uses.py).

This internal caller-owned transaction suffix is not a run/current-permission or
lease admission service. ExecutionInvocationService must load original context
under its command/run guards, supply freshly evaluated scopes, check final lease,
request commit and return only after successful physical UoW exit. No external
effect, resolver, signature or commit is performed here.

The suffix computes original role correlations without selecting a replacement
key, locks both identities and reads both in one statement. Zero rows may create
two; one is conflict; two reload their exact original key registration from the
versioned probe. Current preparation/reauthorization requires active original
keys. Historical exact-key reading remains possible without active selection.

Both distinct references are locked in deterministic order before any distinct
provider rows. Existing authorizer calls then reuse those pinned rows inside the
same UoW. A second-role failure rolls back with the caller; no compensation or
partial repair is added. The same sample-specific command is used for issuance
and each resolution's exact original use; reauthorization requires an existing
complete pair and cannot create a generic third row. Static sender-input checks
validate both public/ref/issuer/provider facts before any metadata return.

The historical pair reader uses exact key/reference/provider registrations and
the same original independently derived context without current active selection.
The SQL invocation-owner helper checks only internal owner/correlation relations;
a digest copied from a probe does not attest a particular check. The service
must independently derive both expected correlations/probes on every check-level
admission, completion and inspection. No SQL store decodes a gateway product.

Independent PostgreSQL tests prove the atomic-store suffix and sibling-check
refusal only. Normal plan admission and actual native/public API composition
remain separate owning proofs; this module opens no profile denial.
