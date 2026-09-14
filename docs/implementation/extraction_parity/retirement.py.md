Source: [extraction_parity/retirement.py](../../../extraction_parity/retirement.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module describes and checks retirement of the mutable legacy tree; it does
not delete that tree. baseline_entries reads every blob path/mode/object ID at
the fixed baseline 5bc3e7debb2aa7f65eeaf3d4c29f030da55d99a8. classify_path assigns
explicit deletion or retention categories and rejects unknown paths. Legacy
package/tests/examples, selected live shells, two demo helpers and root packaging
are deletion categories; current packages/backend/support/parity, reference
runner, evidence, documentation and repository files are retained by policy.

build_manifest requires unique legacy script names from the
[script dispositions](../../../artifacts/extraction/harden-tests-parity-1347-live-script-dispositions.json),
true valid/zero_unowned flags from a completion report and an accepted future-owner
refresh. It records the baseline tree, canonical hashes of those three input
documents and eight fixed post-baseline additions. It does not independently
recompute semantic completion or authenticate the input reports at this boundary.
Zero unowned means recorded disposition/ownership coverage, not that all future
work is implemented.

Future-owner validation requires twelve unique repository/number pairs and allows
open/future-owned or closed/implemented-current entries. Completed issue numbers
must be exactly 1316 and 1317, with truthy merge_commits evidence. It does not
refresh GitHub state, validate those commit objects or require the full historical
set of owner identities. The stored refresh is a dated snapshot, not live status.

validate_manifest rebuilds the entire expected manifest at the fixed baseline
and requires dictionary equality. It then checks Path.exists for each baseline
path: deletion candidates must exist before retirement and be absent with
require_deleted; retained paths must exist in both modes. This does not compare
current file contents, modes or types with recorded blobs, nor exhaustively scan
new paths outside the baseline and fixed additions. Path.exists is not a check
for dangling symlink entries. Normal validation also requires the eight additions
to exist; build mode disables that particular presence check.

Post-deletion validation inspects a fixed list of instruction documents for
retired command strings and selected source roots for AST import/import-from
references to control_plane_kit. It does not cover arbitrary documentation,
dynamic imports or external repositories; missing source roots can yield no
files, while missing listed instruction documents fail. The returned evidence
records path counts and the selected pre/post mode, not test execution or an
independent filesystem/content attestation.

promote_completed_owners is a separate artifact mutation. A fixed mapping replaces
successor/review claims for 24 legacy laws owned by completed issues 1316/1317,
adds named integrity/backend/retirement test records and refreshes source-file
hashes for all additional current tests. It checks source-file existence, not
whether each named method exists or its gate passes. Promotion creates passing
successor records and copies package counts/coordinates from the supplied
declaration; no listed test gate or merged implementation is executed or
authenticated here.

Promotion removes those two future issue numbers from closeout, updates manifest
and input digests/counts, then calls the
[semantic completion validator](../../../extraction_parity/completion.py).
That boundary checks document consistency, identities, recorded passing evidence
and coverage. Only after it returns does promotion write seven files in order:
parity manifest, reconciliation, migration inventory, successor evidence,
aggregate evidence, closeout and completion report. There is no transaction or
rollback across those writes; failure can leave a mixed generation. Repeating a
promotion refreshes current source hashes and replaces its evidence record rather
than appending a new execution history.

JSON/source reads and captured Git output have no overall size bound; Git commands
have no timeout. Writes use a fixed sibling .tmp then os.replace, without backup,
fsync, writer isolation or exceptional temporary-file cleanup. Input metadata and
paths are trusted rather than generally redacted or contained. CLI --build writes
the manifest before physical-path validation; failure can leave that new manifest
beside older evidence. --evidence also writes during ordinary validation.

The historical [retirement evidence](../../../artifacts/extraction/harden-tests-parity-1318-evidence.json)
records 417 deletion and 433 retention paths with legacy_deleted=true. This
review did not regenerate or validate those results. The full 899-line owner and
[235-line tests](tests/test_legacy_retirement.py.md) were read, with selected
completion-validator boundaries and actual artifact context. The
[builder](../build-legacy-retirement-manifest.sh.md) and
[validator wrapper](../validate-legacy-retirement.sh.md) expose different write
surfaces; neither was executed for this companion.
