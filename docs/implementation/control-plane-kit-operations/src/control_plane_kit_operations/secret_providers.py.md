Source: [secret_providers.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py).
Maintain this companion alongside its source.

`authorized_secret_use_for` remains the pure owner of deterministic use evidence.
Its unchanged thirteen-field fingerprint recipe is factored into the private
`_secret_use_fingerprint_for`, shared with the health pair congruence check.
The helper receives already validated semantic fields, including reference and
provider registration IDs, reference ID, intent, actor, correlation and optional
operation/session/run/activity/effect/probe context. The existing sorted compact
JSON/UTF-8/SHA256 `_digest` implementation and `suse_` identity are unchanged;
requested time and lifecycle status were never part of this recipe.

The public producer still validates AuthorizeSecretUse, computes that same
fingerprint and returns the same AuthorizedSecretUse shape. The helper is not
publicly exported and creates no durable use, provider record or authority.
This factoring allows a pure health pair to reject coordinated context changes
without fabricating registered provider/reference rows or duplicating the hash
recipe. Current admission, provider/reference capability provenance and full
actual use reconstruction remain the service/registered owners' responsibility.
No API, schema, lifecycle, signing, material resolution or network behavior is
introduced by this factoring. Existing identity tests and the focused health
pair regression protect unchanged semantics; native corrected execution is
pending at the review checkpoint.
