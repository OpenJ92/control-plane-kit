Source: [build-parity-manifest.sh](../../build-parity-manifest.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This five-line wrapper mounts the current checkout writable in python:3.14-slim,
reads the working-tree copies of reference-law-ownership.json and
reference-demos.json, and writes a newly built parity-manifest.json under
artifacts/extraction. The [builder](extraction_parity/manifest.py.md) requires
matching reference identities but performs no Git-cleanliness or immutable-input
selection. The whole checkout is writable even though the current call targets
one generated artifact.

Rebuilding is initial construction: every entry receives empty successors and
null supersession. The wrapper neither reads the existing manifest nor preserves
its later completion evidence. A populated parity ledger is therefore replaced,
not incrementally updated. The current output artifact already contains successor
and supersession records; this documentation review leaves them intact.

The container may require a mutable Python image pull. --rm requests ordinary
container removal, while the output persists on the host. The writer uses a
fixed .tmp sibling and replacement without backup, concurrent-writer isolation
or fsync durability. Successful generation means structural construction only;
it does not run tests/demos, verify evidence records or establish parity.

[Cross-document validation](../../extraction_parity/validation.py) has the separate
role of comparing an existing ledger with expected mappings and evidence. Its
use of build_manifest for an in-memory expected mapping is not permission to
overwrite the maintained artifact through this shell. No wrapper, build or
artifact mutation was executed for the companion.
