Source: [delegation_signing_keys.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_signing_keys.py).
Maintain this companion alongside its source.

The lifecycle service consumes Core's closed health correspondence at both
registration and activation. For example, a registration command with purpose
`DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ` requires an actively admitted
private-key reference allowing `SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY`.
Gateway health transit requires its distinct transit intent. Probe-only, opposite
health, and old control/transit intents do not substitute for either health intent.

The four explicit legacy purposes retain their existing probe-intent admission
policy. The private selector checks the nominal enum before membership and has
no fallback for unknown or future purposes. It names admission requirements,
not permission to sign or dispatch a health request.

Existing register/activate scopes remain distinct. Issuer, workspace, purpose,
key ID, public identity and private reference retain their original immutable
binding. Stores serialize lifecycle changes using existing purpose/issuer locks
and the unique active-key index. Each command owns one UOW and commit; retry
returns original records, activation preserves overlap, and denial leaves prior
active/verify-only state intact. Reference checks keep their existing
point-in-command semantics; this change does not add a revocation-concurrency
guarantee or postcommit authority. The later immediate-use reload owns that proof.

Tests in `test_delegation_signing_keys.py` exercise both families, exact replay,
purpose/workspace isolation, wrong-intent refusal, distinct scopes and reference
change/revocation before activation. Existing old-family lifecycle laws remain.
Records contain public identity and references, never private material. No new
provider call, route, scope, network exposure or activity-history format is added.
