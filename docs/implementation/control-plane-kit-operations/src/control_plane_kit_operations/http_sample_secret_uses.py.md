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

This stage does not integrate the reached-check port or open either existing
sample-profile denial. Independent PostgreSQL tests prove only the atomic-store
suffix. Supported typed capability, original history/current permission/lease,
normal plan admission and native signing/transport remain owning integration.
