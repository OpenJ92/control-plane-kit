Source: [test_support/tests/test_package_integrity.py](../../../../test_support/tests/test_package_integrity.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These root self-tests import inspect_package directly from
[package_integrity](../package_integrity.py.md). The [Core gate](../../../../control-plane-kit-core/test.sh)
mounts test_support and runs from that directory so this import resolves. Each
test creates its own temporary src/tests/gate/approval tree and removes it in
tearDown; it asks the scanner to inspect text rather than importing or running
the synthetic test module.

Cases cover hidden files/free functions/nested classes, directly aliased TestCase
and skip names, unconditional/literal/unapproved skips, valid dynamic approval,
duplicate/blank/stale approvals, pass and ellipsis placeholders, swallowed
exceptions, legacy/pytest imports and a proof-changing gate variable. Allowed
empty helper classes/context bodies prevent a blanket pass-statement ban.
Mock evidence remains a location record rather than a finding. The dynamic
condition() fixture need not exist because the scanner does not evaluate it.

Negative cases generally assert presence of a finding code, not the entire
report. The positive package case checks validity; explicit identity equality
appears for matched skip approval and exact mock location, not an exhaustive
discovery inventory. These tests do not exercise CLI output/exit handling,
resource bounds or all alias, malformed-input and filesystem paths.

The complete 211-line owner and the actual root scanner were read. Unlike the
SDK's separate self-test harness, this file uses direct import and per-test
setUp/tearDown rather than loading a module from CPK_PACKAGE_ROOT. Preserve
these actual fixture and assertion differences when maintaining related copies.
No executable validation ran for this documentation.
