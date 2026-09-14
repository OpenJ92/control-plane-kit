Source: [build-legacy-retirement-manifest.sh](../../build-legacy-retirement-manifest.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper first invokes the [retirement module](extraction_parity/retirement.py.md)
with --promote-completed-owners. That operation rewrites seven related parity,
reconciliation, inventory, evidence and closeout/report artifacts after semantic
consistency validation. It uses recorded promotion claims and refreshes source
hashes; it does not run the named tests. This mutation occurs before the separate
retirement-manifest build and must not be mistaken for a single-output generator.

The second invocation builds the issue-1318 manifest and writes retirement
evidence. If control_plane_kit is a directory under ROOT_DIR it chooses the
pre-deletion check; otherwise it adds --require-deleted. That one directory test
selects the mode, while the Python validator subsequently checks individual
baseline paths. Neither mode deletes source files. Build mode writes its manifest
before physical-path validation and omits the normal eight-addition presence
requirement, so it differs from the standalone validation wrapper.

Both calls use host python3 and ambient module resolution. ROOT_DIR anchors data
paths, but the shell does not cd there or set PYTHONPATH; invocation therefore
depends on the caller's working directory/environment finding the intended module.
There is no Docker isolation, pinned interpreter or wrapper-level timeout.

set -e stops on failure without reverting earlier artifacts. Promotion's seven
sequential writes and the subsequent manifest/evidence writes do not form one
transaction, and the wrapper adds no backup, cleanup or concurrency protection.
The stored [manifest](../../artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json)
records the historical retirement decision; rerunning this wrapper can rewrite
its related evidence.
The full 23-line wrapper, module and owning tests were read. No Python invocation,
promotion, artifact rewrite or filesystem retirement ran for this companion.
