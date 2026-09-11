Source: [control-plane-kit-core/tests/approved_skips.json](../../../../control-plane-kit-core/tests/approved_skips.json).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This authored test-policy document currently contains an empty approval list.
The [shared integrity checker](../../../../test_support/package_integrity.py)
reads it through the package harness and validates named approvals/reasons.
Empty means there are no listed skip approvals; it is not proof that the suite
was collected or passed.

An added entry changes executable evidence policy, so it needs the governing
issue/review reason rather than being a convenient way to make a failing suite
green. The checker owns the entry contract; this file is not a generated result
or a per-run test report.
