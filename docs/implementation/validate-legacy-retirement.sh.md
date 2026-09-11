Source: [validate-legacy-retirement.sh](../../validate-legacy-retirement.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper invokes the [retirement validator](extraction_parity/retirement.py.md)
against fixed issue-1318 manifest/evidence paths. Only a first argument exactly
equal to --require-deleted selects post-deletion validation; other arguments are
not rejected or forwarded. With no recognized flag it requires deletion-candidate
paths still to exist. It does not infer mode from the current tree or delete files.

Unlike the [builder](build-legacy-retirement-manifest.sh.md), it performs no owner
promotion or manifest regeneration and requires the eight post-baseline additions
to exist. It still writes the evidence file after successful validation through
the module's fixed .tmp-and-replace writer. Thus this is not a read-only check.
Failure before that write leaves any previous evidence in place; shell exit
status and the new invocation's result must not be inferred from an old file.

Host python3 uses the caller's working directory/PYTHONPATH for module resolution:
passing ROOT_DIR does not pin the imported module or interpreter. The wrapper
adds no timeout, input snapshot, backup or multi-writer protection. Validation
checks baseline policy, path presence and selected instruction/import references;
it does not rerun recorded package tests or refresh remote issue state.

The full 16-line owner and called validation boundaries were read. No wrapper,
Python validation, evidence write or deletion was executed for this companion.
