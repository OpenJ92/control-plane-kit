Source: [current_backend/tests/test_source_lock.py](../../../../current_backend/tests/test_source_lock.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

Owns the exact-source-selection and archive-boundary laws of
[source_lock.py](../source_lock.py.md): upstreams derive from the locked Servers
manifest, mutable checkout edits do not affect materialization, identity and
missing-object substitutions fail, unsafe/duplicate archive paths are rejected,
and temporary trees disappear on the exercised success/failure paths.

The fixture creates and commits local Git repositories and may construct lock
values with file URLs. That exercises clone/resolve behavior without proving
production JSON-loader URL admission or remote authentication. Fixture-generated
commit identities are examples; the checked-in lock assertion intentionally
tracks the selected root coordinate and changes with its adoption.

This is effectful test apparatus (temporary files and Git processes), not just
assertions. Use the owning Docker-backed validation described in
[TESTING](../../../TESTING.md); this note does not authorize host execution.
Cleanup assertions cover the fixture paths, not all OS cleanup failures or live
Docker/provider residue. Do not promote source-selection green into deployment
acceptance.
