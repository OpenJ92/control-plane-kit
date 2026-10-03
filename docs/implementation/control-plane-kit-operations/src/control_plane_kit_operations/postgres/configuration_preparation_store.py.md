Source: [configuration_preparation_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/configuration_preparation_store.py).
Maintain this companion alongside its source.

#1923 adds original configuration provenance inside Operations. Own original configuration refs and protective claims inside the existing first-start transaction.

The first-start owner rederives selected material under the existing lifecycle lock before IDs or writes. Exact original replay reads retained intent and protective claims without selecting current catalogs. Public evidence states are complete, unavailable, or capacity; none authorize dispatch, reuse, acceptance, deletion, or release.

Transport reservations include statements, scalar markers, returned values and relational identities. Failed reads retain their reservation. Caches stay within one UoW; command accounting can span the coordinator and nested start while fresh mutable authority is reread.

B1 source integration is implemented. The full owning Docker gate and independent whole-B1 review remain pending. B2 current-use evidence and provider behavior are outside this module's authority.

The future read reservation keeps the full 8,192-byte outcome domain. Per ref,
192 KiB includes an 80,032-byte compact-source fetch, a 32,768-byte ref row,
480-byte claim coordinates, the 8,192-byte outcome, two 16,384-byte event bodies,
and eight 2,048-byte ancillary coordinates (170,624 value bytes). The reserved
24 relational identities, 384 scalar markers and 24 statements add 15,360
accounted bytes, leaving 10,624 bytes of slack in that per-ref allowance. These
are conservative limits, not a claim that all maxima are fetched on every read.
The separate 3 MiB snapshot plus 512 KiB plan allowance includes 264 additional
relational identities; its row cost is deducted from available value bytes.
The current command's already-used footprint is added without resetting it.
Any later consumer must still obey the global 4,096-identity/16 MiB limit; B1
neither implements nor grants B2/current-use or cleanup authority.

B2's advancement evidence scope roots the existing command ledger before the
first locator read and includes original replay. It translates closed capacity
or unavailable failures after the handler into bounded advancement conflict;
there is no connection wrapper, independent commit or acceptance authority.

B2 extracts the existing pure complete ref/claim material check as `_decode_ref`.
The existing `_decode` still performs the original compact source proof afterward;
its result and callers retain their contract. Acceptance can validate complete
manifest material before selecting source provenance work. First-start preparation
and claim insertion remain unchanged in this initial nonempty slice; reuse is
still refused until its corresponding current-use proof is implemented.
