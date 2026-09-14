Source: [current_backend/tests/approved_skips.json](../../../../current_backend/tests/approved_skips.json).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The file is an empty approved-skip list. The repository's
[integrity tool](../../../../test_support/package_integrity.py) understands lists
of exact test identities/reasons when explicitly given their approval path;
empty means no exceptions recorded for that scan.

The current [runner unit stage](../runner.py.md) invokes unittest directly and
does not load this JSON or run integrity over current_backend/tests. The file
therefore does not itself enforce absence of unittest skips or establish that
all backend tests ran. Changing it has no automatic effect on that stage. Keep
recorded approval data separate from actual gate wiring; no new scan, approval
policy or execution authority is introduced by this companion.
