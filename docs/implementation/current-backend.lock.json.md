Source: [current-backend.lock.json](../../current-backend.lock.json).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This authored lock is a selection contract, not a generated dependency dump.
It chooses one immutable Servers commit; that commit's coordinate manifest
provides the upstream Core/Operations, Interpreters and Secrets versions. The
selected commit is therefore not necessarily the latest branch of any repository.

[source_lock.py](current_backend/source_lock.py.md) owns parsing and derivation;
[test_source_lock.py](current_backend/tests/test_source_lock.py.md) includes the
checked-in root-coordinate assertion. An adoption changes the selected source
set and requires reviewing its actual composition and affected companions, not
merely replacing a hash until a test passes. The lock itself grants no provider
permission and proves no packaged-image or deployment result. Do not duplicate
upstream commits here independently of their owning Servers manifest.
