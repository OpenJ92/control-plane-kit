Source: [control-plane-kit-operations/tests/approved_skips.json](../../../../control-plane-kit-operations/tests/approved_skips.json).
Maintain this document alongside its source. Recheck the integrity scanner and
review requirements before changing the exception list.

The complete file is the empty JSON list []. Operations currently declares no
approved skip identities in this manifest. This is static package-integrity input,
not a test result, runtime configuration or authorization for provider actions.

The [package runner](../../../../control-plane-kit-operations/test.sh) invokes
[package_integrity.py](../../../../test_support/package_integrity.py) with the
mounted package as /source, src/tests roots and test.sh as its gate file. The
integrity CLI defaults --approved-skips to tests/approved_skips.json relative to
that package root. It reports findings and returns a nonzero exit code on failure.
The runner reads this before starting the suite's PostgreSQL container, although
the enclosing shell has already registered its cleanup trap.

The consumer reads JSON rather than importing this file. An absent file or None
path is treated as an empty list too; existence is not enforced by that reader.
A non-list root, non-object entry, blank identity, blank or over-240-character
reason, or repeated exact identity yields an integrity finding. The reader stores
the supplied strings without trimming them and ignores additional object fields.

Approval matching uses package-root-relative path::Class or
path::Class.test_method identities, for example
tests/test_example.py::ExampleTests.test_value. For recognized nonliteral conditional skip
decorators, the identity and literal reason must match the manifest exactly.
Unconditional skip decorators and literal-boolean conditions are rejected before
approval lookup. Unencountered approvals are reported as stale. Thus adding an
entry is not a general bypass for skipped tests or other integrity findings.

The empty list does not prove that every test executes or passes. The scanner
recognizes selected unittest class/decorator syntax; runtime skipping, dynamic
wrappers and test collection still have their own behavior. No new exception is
authorized by this companion. Keep changes tied to the governing reviewed test
contract rather than using entries to hide a failure.

Read depth: full one-line manifest; selected actual consumer dataclasses, JSON
reader, class/method identity and decorator matching, stale checks, CLI and exit
status; runner integrity invocation; selected scanner tests for unconditional,
unapproved, literal and accepted conditional skips. Those synthetic tests are
separate from Operations execution. No full scanner audit or executable validation
is claimed. This note changes no source, tests, dependencies or security surface.
