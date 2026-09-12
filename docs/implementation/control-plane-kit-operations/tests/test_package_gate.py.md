Source: [control-plane-kit-operations/tests/test_package_gate.py](../../../../control-plane-kit-operations/tests/test_package_gate.py).
Maintain this document alongside its source file. When package-gate dispatch,
repository evidence, prerequisite checks or fixture diagnostics change, verify and
update this companion in the same change.

This 597-line unittest file contains 13 tests for the Operations package harness.
It combines textual assertions over repository files with execution of the actual
test.sh through temporary fake docker, git and sleep executables. This checks shell
control flow and emitted command arguments. The fake Docker command never executes
the container payload, starts PostgreSQL, installs dependencies, imports Operations
or runs the nested behavioral suite. Passing this file is therefore distinct from
passing the real owning package gate.

The complete test, complete 132-line
[Operations gate](../../../../control-plane-kit-operations/test.sh), complete
[CI workflow](../../../../.github/workflows/tests.yml), both package pyproject.toml
files, the complete [Core gate](../../../../control-plane-kit-core/test.sh), and
[TESTING.md](../../../../docs/TESTING.md) were read. Selected portions of
[package_integrity.py](../../../../test_support/package_integrity.py) were inspected
for its report shape, gate-option scan and CLI dispatch, not its full inspection
algorithm or tests. The external architecture-testing checkout was not inspected,
and this review did not read every production Python file scanned by the test.
Nothing was imported or executed for this documentation slice.

The gate imports no application module into its shell process. Its normal container
sequence is integrity checking, PostgreSQL launch/health wait, unittest discovery,
compileall, then clean installed-package import from /tmp. The Python image defaults
to python:3.14-slim with an environment override; PostgreSQL uses postgres:16-alpine.
Behavior, compile and clean-import containers copy source into temporary directories
and install Core and Operations before their final command. The test's phase names
refer to these command payloads, not observed completion of installation or behavior.

Before Docker dispatch, the actual gate requires a directory for architecture testing,
compares git rev-parse HEAD with
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`, and rejects nonempty short status including
untracked files. Its default is the sibling control-plane-kit-architecture-testing
checkout; CPK_ARCHITECTURE_TESTING_ROOT overrides that location. The tests model two
rejection cases, wrong commit and dirty status, and assert nonzero exit with no
recorded phase. They do not model a missing directory, failed Git command, forged
checkout contents or submodule state. The fake Git executable only checks its -C
target and supplies controlled rev-parse/status responses; it does not inspect Git
objects or prove that the external dependency actually has the accepted content.

The CI test reads workflow text and splits it at the exact operations job marker.
It extracts steps using indentation-sensitive name strings, then checks the
architecture repository name, exact commit, checkout path, persist-credentials:
false, and the gate's CPK_ARCHITECTURE_TESTING_ROOT expression. It also requires the
pre-operations portion to lack the architecture repository name. This is not a YAML
parser, workflow execution, successful checkout or a credential audit. The actual
workflow has separate Core and Operations jobs invoking their respective package
gates; it also sets read-only contents permission and job timeouts, which this test
does not independently assert.

Repository evidence has explicit path helpers. CPK_TEST_WORKFLOW_PATH chooses the
workflow, CPK_CORE_SOURCE_ROOT chooses Core source, and CPK_OPERATIONS_SOURCE_ROOT
chooses Operations source. Defaults come from the file's resolved package/repository
location. The copied-package test patches these three environment variables to
temporary paths and checks that the helpers return them. It does not create or read
the target workflow/source trees, so it proves path selection rather than successful
copied-package execution. test.sh itself is always read/run from PACKAGE_ROOT.

Two dispatch tests inspect the fake Docker transcript for five expected phases.
Only unittest must carry the architecture-testing read-only mount and its PYTHONPATH;
the gate text must contain each exactly once and omit literal git+https and pypi.
Another matrix requires workflow and TESTING.md file mounts plus their environment
variables and the Core/Operations source-root variables only in unittest. The actual
behavior command additionally receives the module inventory and read-interface
document; this test's evidence matrix does not enumerate those additional mounts.

_forbidden_evidence_mounts recognizes a lexical repository-root source prefix or
the exact read-only whole /cpk-test-evidence destination. Its five-volume canary
expects only the first two broad examples to be rejected, allowing package, Core
and test-support source mounts. Assertions cover the extracted -v arguments for
the expected phases. This is not filesystem path canonicalization, symlink checking,
Docker mount enforcement or a general detector for every way to expose repository
content. The transcript's run-argument checks use substring membership, while volume
checks preserve each argument that followed -v.

Production dependency separation is checked textually. The test reads both
[Core metadata](../../../../control-plane-kit-core/pyproject.toml) and
[Operations metadata](../../../../control-plane-kit-operations/pyproject.toml),
rejects the literal distribution name control-plane-kit-architecture-testing, scans
all *.py files under their src roots for the literal import spelling, and rejects
architecture-testing in Core's gate. This detects those spellings, including comments;
it does not parse dependency resolution, evaluate dynamic imports, inspect wheels or
prove that the enumerated source trees are complete. Operations currently declares
Core and psycopg dependencies; Core declares Jinja2, PyYAML and rfc8785. The accepted
architecture commit does not pin all runtime/build dependencies or image digests.

The clean-import test only asserts that the shell file contains
`find_spec("control_plane_kit_architecture_testing") is None`. The actual clean-import
payload imports Operations, checks version 0.1.0 and fails when find_spec can locate
architecture testing. The fake phase does not execute this payload. Its absence
from the phase's mounts/PYTHONPATH and the textual assertion support harness shape;
only running the real clean-import container can supply installed-environment proof.

Fixture tests require the shell text to use docker rm -fv and a 2 GiB tmpfs mount
for PostgreSQL data. A fake-run test compares the entire PostgreSQL launch argument
tuple: default container/network names, tmpfs flags, fixed disposable database/user/
password settings, health command and intervals, postgres:16-alpine, and settings
max_wal_size=512MB, checkpoint_timeout=2min and log_checkpoints=on. These assertions
verify command configuration, not actual disk/WAL consumption, authentication strength,
container startup, volume deletion or successful network cleanup.

The actual gate installs its EXIT cleanup trap after prerequisite checks. Cleanup
requests removal of the named PostgreSQL container including volumes, then removal
of the named network, ignoring cleanup failures. It also calls cleanup before
creating its fixture. Names can be overridden by environment; these tests assert
the defaults and do not establish ownership of a real resource with either name.
The fixture uses no host port mapping in the inspected launch. Private Docker
networking is not a substitute for application authentication.

Four tests cover success and diagnostic flow with controlled fake outcomes:

- A unittest status of 37 must propagate, with the synthetic database log line in
  stderr. Only integrity, postgres and unittest phases occur; the log command must
  follow unittest and precede final container/network cleanup.
- A log-command status of 19 must not replace unittest status 37. Container and
  network removal each appear twice, corresponding to prelaunch and EXIT cleanup.
- A permanently unhealthy fake fixture must return 1, print the health failure,
  request logs before final cleanup and never dispatch unittest/compile/clean-import.
  Fake sleep returns immediately; this case does not measure real startup time or
  independently assert the number of health polls.
- Success must produce exactly the five expected phase events, no log command or
  synthetic root-cause line, two container removals, two network removals and one
  network creation.

The inspected real loop polls up to 60 times with sleep 1 and performs a final
health check. Its failure and unittest-error branches request
`docker logs --timestamps --tail 400` before exiting; log failure is tolerated.
The tests' bounded-log claim concerns this tail count, not a byte bound or secret
redaction. They inject neither integrity/compile/clean-import failures nor Docker
launch/inspect/network/removal failures, signals or interrupted cleanup. Consequently
they do not prove all error paths preserve status or leave no runtime residue.

_run_gate creates a TemporaryDirectory, writes executable shell stubs and an events
file there, and creates an empty directory representing architecture testing. Docker
run classification scans every argument for recognizable payload strings. Unknown
run/command forms return 99; inspect returns the selected health word; logs print
postgres-root-cause and return a selected status; rm/network merely append events.
Only the unittest run phase has an injected nonzero phase status. Git returns selected
commit/dirty evidence, and sleep performs no delay. The helper prepends these stubs
to inherited PATH, overrides its fake controls and architecture root, then runs
`sh <PACKAGE_ROOT>/test.sh` with captured stdout/stderr and check=False. There is no
subprocess timeout. It reads transcript lines before the temporary directory closes.

This is actual local shell/file/subprocess behavior inside the test, with simulated
Docker and Git boundaries. The environment is copied rather than fully sanitized;
unoverridden gate image/network/container settings can affect expectations. Stubs
do not validate general Docker syntax, mount existence or the payload's contents.
Phase maps retain the last run entry per phase in the exposure tests; the separate
success test checks the complete phase sequence. The file imports only standard
library modules and has a normal unittest.main entry point, without opening a
database connection itself.

The integrity phase in the real harness dispatches package_integrity.py with the
Operations package root, src, tests and test.sh. The inspected CLI invokes its
inspection function, reports findings and returns nonzero when findings exist.
These package-gate tests record that dispatch but do not run the inspector or test
its full detection rules. The source scan, workflow checks, fake transcript and
actual owning Docker suite provide different evidence and must remain distinguished.

The owning validation remains the normal Operations package gate with its documented
prerequisites. This companion adds no execution permission, no provider or credential
access, and no new security or durable-data behavior. Its textual validation does
not count as package, composition, published-image or live acceptance evidence.
