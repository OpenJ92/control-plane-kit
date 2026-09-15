Source: [current_data_validation.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py).
Maintain this companion alongside its source.

Exact-current installation validates retained graph lineage and owner rows using
bounded read-only scans. Health preparations now participate through their store's
private validator after the existing attempt and original intent owners. It scans
at most eight preparation rows per keyset page and uses the same canonical and
retained-join reconstruction as normal reads. Oversized copied text/preimages are
bounded before transfer and invalid history refuses current installation.

No current authority is inferred from historical evidence, and unrelated attempts
need no preparation. Installation never repairs an incompatible retained store,
creates missing projections, rewrites evidence or performs a provider effect.
The caller retains the transaction; schema drift and data drift both refuse.
