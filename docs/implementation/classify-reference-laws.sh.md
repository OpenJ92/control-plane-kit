Source: [classify-reference-laws.sh](../../classify-reference-laws.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper runs the current [ownership classifier](extraction_parity/ownership.py.md)
in python:3.14-slim. It locates the repository, mounts the whole checkout at
/workspace, reads the working-tree copies of reference-tests.json and ownership-rules.json,
and replaces reference-law-ownership.json under artifacts/extraction. Paths are
fixed relative to that mounted repository; this command does not select or
revalidate a frozen Git tag, discover tests or run a migration.

The container mount is writable, broader than the single artifact path that the
current Python call writes. --rm requests container removal on ordinary exit;
the generated ownership artifact persists in the checkout. Docker may pull the
mutable Python tag, so this is an effectful artifact-generation command rather
than a pure read or an immutable toolchain reproduction. The writer's fixed .tmp
sibling does not protect concurrent writers or guarantee crash durability.

The artifact inherits historical reference identity/count fields from its input.
Successful classification proves neither that the input was freshly collected
nor that any current successor tests pass. Review changes to the input rules and
output together through the owning rollout; these labels grant no provider,
credential or destructive-runtime authority. The companion review did not run
the wrapper or regenerate artifacts.
