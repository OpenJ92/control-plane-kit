Source: [current_backend/source_lock.py](../../../current_backend/source_lock.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

## Responsibility and change-sensitive contracts

This owner resolves one immutable multi-repository source selection. The recorded
motivation is in [Current Backend Validation](../../../current_backend/README.md):
Servers' selected coordinate manifest owns upstream versions; another lock must
not independently choose incompatible Core, Interpreter or Secrets commits.

`BackendLock` fixes a Servers commit and the expected upstream coordinate keys.
`_read_upstream_commits` reads that manifest through `git show` at the selected
commit, not from the mutable checkout. `resolve_local_backend` verifies repository
identity and required commit objects; `materialize_backend` archives those objects.
Changing the checkout's HEAD or editing its files must not change the selected
source. This selects source, not compatibility, authorization or live success.

The JSON-loading path additionally requires exact fields, HTTPS GitHub provenance
and matching URL/identity. Do not infer those loader checks from direct dataclass
construction: tests intentionally construct `RepositorySpec` values with local
file URLs. Archive extraction rejects unsafe paths, duplicate members, occupied
destinations and non-file/non-directory members. Keep those negative boundaries
when changing Git or archive handling; do not substitute extraction of arbitrary
links or a copy of the working tree.

## Effects, consumers and limits

`clone_backend` fetches Git objects into temporary stores; local resolution reads
Git metadata, and materialization writes temporary extracted trees. These are
filesystem/network effects, not a pure graph transformation or a Docker provider
operation. Context cleanup calls `shutil.rmtree(..., ignore_errors=True)`; it is
best effort, not a verified residue audit or permission to delete other paths.

Known consumers are [contracts.py](../../../current_backend/contracts.py), which
reads `MaterializedBackend`, and [runner.py](../../../current_backend/runner.py),
which owns execution and reporting. These links are non-exhaustive. Preserve the
separation between selecting exact source, checking composition and running it.

[BackendSourceLockTests](../../../current_backend/tests/test_source_lock.py)
exercise derived commits, dirty-checkout independence, identity/missing-object
rejection, archive rejection and ordinary cleanup paths. Local fixture clones
do not prove remote GitHub access or provider deployment. Verification here is
source review; no new execution result is claimed by this companion.
