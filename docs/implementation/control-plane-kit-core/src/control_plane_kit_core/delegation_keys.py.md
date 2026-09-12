Source: [delegation_keys.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py).
Maintain this companion with source and imported contract changes.

The closed key-purpose language now includes WORKLOAD_NODE_HEALTH_READ. This
purpose separates admitted health callbacks, which may read protected
dependencies, from static surface disclosure. Existing purpose strings and
public-key material laws remain unchanged. The enum is a pure contract; it does
not provision keys, authorize signers, migrate a schema or resolve secrets.

Current backend purpose checks and SecretUseIntent need explicit downstream
adoption under Operations #1821 / Interpreters #149 and their proper owners.
Do not alias an old purpose or signing intent to bridge that gap. Health grant
constructors/codecs admit only the new exact purpose/profile pair, while old
verifiers retain their acceptance domains.
