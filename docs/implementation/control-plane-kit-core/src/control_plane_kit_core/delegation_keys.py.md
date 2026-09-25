Source: [delegation_keys.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py).
Maintain this companion with source and imported contract changes.

The closed key-purpose language now includes WORKLOAD_NODE_HEALTH_READ. This
purpose separates admitted health callbacks, which may read protected
dependencies, from static surface disclosure. Existing purpose strings and
public-key material laws remain unchanged. The enum is a pure contract; it does
not provision keys, authorize signers, migrate a schema or resolve secrets.

Core #1841 adds the two corresponding SecretUseIntent members and the closed
`secrets.health_signing_intent_for` transformation. Current backend purpose checks
and provider/signing consumers still need explicit downstream adoption under
Operations #1842/#1846, Secrets #29 and Interpreters #149.
Do not alias an old purpose or signing intent to bridge that gap. Health grant
constructors/codecs admit only the new exact purpose/profile pair, while old
verifiers retain their acceptance domains.

Core #1827 adds GATEWAY_NODE_HEALTH_READ_TRANSIT independently from both old
variable transit and WORKLOAD_NODE_HEALTH_READ. Schema, provider intent adoption
and retained key/grant resolution for both health purposes remain explicit later
owner work. Core correspondence grants no signing authority. The old binary
signing fallback must not receive these purposes as if they were existing
workload credentials.
