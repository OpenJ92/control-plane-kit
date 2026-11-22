Source: [product_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/product_store.py).
Maintain this companion alongside its source.

Configuration preparation reads exact product references selected by both
pinned graphs. The original complete-row decoder still validates reference,
source and descriptor commitment; descriptor_content remains text. Bounded
probes/guards share the configuration command budget. Fresh work observes current
revocation, while original-attempt replay never performs a fresh product scan.

Workspace product reads use `directory_page` for active summaries, ordered by
exclusive immutable descriptor digest with `limit + 1` lookahead (at most 101
rows). SQL bounds and selects summary fields without transporting descriptor
bodies or registration metadata. `directory_detail` selects one exact workspace
and digest, including revoked admissions, with the existing 262144-byte
descriptor ceiling. It reconstructs and validates the original reference rather
than hashing display data. These methods do not commit, mutate registrations,
fetch images, or grant cross-workspace access; the application owns authorization.
