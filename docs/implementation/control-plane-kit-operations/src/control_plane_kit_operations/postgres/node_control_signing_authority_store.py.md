Source: [node_control_signing_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_signing_authority_store.py).
Maintain this companion alongside its source.

The private store retains the old closed
`get_for_share(NodeControlIntendedAttempt)` entrance and adds
`get_health_for_share(HealthEffectPreparationRecord)`. They extract only the
workspace and two exact saved authorization IDs into one private scalar query.
No generic purpose resolver or new public store/bundle field is introduced.

Both entrances use the same84-column single-row query and the same six
`FOR SHARE` authorization/reference/provider row locks. Active chain joins,
workspace/reference/provider identity, timestamp decoding and bounded malformed
row errors are unchanged. Driver execution remains outside decoding catches;
existing owner/driver exception identity is preserved. The store makes no
commit, write or schema change. Distinct variable and health services own their
respective current-context and explicit-purpose decisions; sharing SQL does not
widen the old variable API or import its policy into health reload.

## O2 / #1883 current boundary

The health-only locked chain query accepts ReceiverHealthEffectPreparationRecord. The existing six-row key-use/reference/provider query and exception/locking behavior remain; ordinary node-control signing is unchanged. Historical V1 preparation reconstruction does not call this live query.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.
