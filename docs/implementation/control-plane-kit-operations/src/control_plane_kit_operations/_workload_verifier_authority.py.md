Source: [_workload_verifier_authority.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_workload_verifier_authority.py).
Maintain this companion alongside its source.

The private Operations selector shares the existing public verifier read's current
authority semantics with fresh refresh admission. It acquires all requested
purpose locks in fixed order, requires one exact active signer per purpose, then
reads only that issuer's active/verify-only keys with a 17-row sentinel and
16-key limit. It returns existing immutable public family values, never private
references or a signing lease. Expected authority/codec failures become fixed
detached errors; unexpected owner errors retain identity.

The guard consumes an already canonical managed-update plan. It inspects the
selected wrapper of a retained Reconcile target and does nothing if its verifier
families and declaration are unchanged. Changed families, or unchanged families
accompanying a declaration successor, must equal the current owner-selected
snapshot in workspace, purpose, issuer and full public key identity. It does not
duplicate Core's graph relation or registered-product admission. The actual
desired wrapper retains Core's strict size bound; the read projection retains
its existing 64 KiB envelope bound. The separately bounded key-owner reads do
not imply a new global configuration-provenance memory bound.

Fresh planning calls the guard before plan/action IDs or writes. Fresh configuration
preparation invokes it both optimistically and again under the real start owner's
lifecycle lock; purpose locks remain held through that UoW commit. A changed
snapshot is refused, never silently reauthored. Exact original replay returns
before this path. Existing health reload keeps the same current signer, saved
coverage and original intervals; adding a compatible verify-only key after
preparation neither renews nor alone invalidates a grant. Result/fold retain
original installation evidence even if current key truth subsequently changes.

For Core's admitted coupled declaration/route pair, the workload's full public
snapshot is checked before both workload and gateway fresh installation. The
gateway's own wrapper is not authority for the workload. Activity filtering occurs
after selecting the workload declaration relation so a gateway first start cannot
skip that obligation. Existing add/remove and verifier-only behavior stays with
the same canonical profile and owner checks; parsed route content grants no power.

No schema, key lifecycle, provider IO, new scope, grant representation or runtime
effect is introduced. Unknown installation and failed protected health retain
their existing refusal of advancement, without automatic rollback or redispatch.
