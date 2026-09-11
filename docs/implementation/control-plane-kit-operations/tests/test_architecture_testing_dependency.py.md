Source: [control-plane-kit-operations/tests/test_architecture_testing_dependency.py](../../../../control-plane-kit-operations/tests/test_architecture_testing_dependency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three tests check a small architecture-testing integration example and the
Operations suite's documentation input. They do not inspect Operations application
architecture or run provider effects. The synthetic sample is two lines of Python:
an aliased import of sample.tools.inspect, followed by inspect_value(). It is
passed as text to the shared analyzer, not imported or executed as a sample module.

The dependency helper dynamically imports control_plane_kit_architecture_testing.
If ModuleNotFoundError names exactly that package it returns None; the first test
then fails its availability assertion. A missing transitive module is re-raised,
and other import errors are not normalized. Those error branches are source
behavior, not separately exercised negative cases. The assertion message says
"exact" dependency, but the test does not check a version, commit or module origin.

The sample's import policy names the module, imported symbol and local alias; its
call policy expects ResolvedCallTarget("sample.tools.inspect"). Both evaluations
must return the empty tuple. A second call policy with no expected calls must
return exactly one finding of exact type PolicyFinding, with the supplied fixed
message. The full sample source string must not occur in repr(findings). This is
one selected non-echo check, not a bound on repr, universal source redaction or
assertion about every finding field. No negative import-policy case, duplicate
occurrence, malformed facts, ambiguous alias or batch evaluation is covered here.

The actual architecture-testing checkout used by the harness is
7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef. Its public exports bind analyze_source
to the stdlib-AST analyzer and evaluate_policy to the shared exact-policy owner.
The inspected analyzer parses source and derives import/alias/call facts; the
evaluator admits matching path/module coordinates and compares sorted projected
occurrences with the policy's expected tuple. A mismatch produces a policy-authored
finding at the module anchor. This explains the test's alias example without
claiming dynamic call resolution, execution reachability or validation of CPK's
own architecture policies. The test title's "without cpk policy" means this example
constructs generic policies; it does not inspect loaded modules for policy absence.

The document helper defaults to docs/TESTING.md under the repository root inferred
from this test's path, or uses CPK_TESTING_DOCUMENT_PATH when set. The documentation
test reads UTF-8, collapses whitespace and requires eight literal substrings naming
the architecture-testing repository, exact clean sibling acquisition, harness/CI
agreement, preparation before the suite, the read-only mount and prohibitions on
host installation or substituted coordinates. It does not parse those instructions,
verify their ordering or enforce them against Git, Docker or CI state. Missing or
unreadable document inputs are ordinary test errors, not converted findings.

The final test patches that environment variable to a path inside a temporary
directory and checks only that the helper returns the same Path. It does not create
or read the proposed TESTING.md file, copy a package or test fallback after the
patch. Its "explicit copied package evidence" name should not be mistaken for
proof of a complete copied-package test run.

The actual [package harness](../test.sh.md) supplies that copied-package context:
it rejects a missing, wrong-commit or dirty architecture-testing checkout before
Docker, mounts its source read-only and exposes it through PYTHONPATH in the
behavior container. It separately mounts the root testing document read-only and
sets CPK_TESTING_DOCUMENT_PATH so the copied test does not rely on a copied repo
layout. The [CI workflow](../../../../.github/workflows/tests.yml) checks out the
same commit and passes its location to the unmodified gate. These are inspected
harness/CI contracts, not actions performed by the three tests.

[Packaging metadata](../pyproject.toml.md) does not declare architecture-testing
as an Operations runtime dependency. The harness's later clean import container
requires it to be undiscoverable; that separate gate assertion is not part of this
file. Follow [TESTING.md](../../../TESTING.md) for Docker-only validation and the
missing-prerequisite stop rule. No host installation or alternate harness is
authorized by the document-path override or this companion.

Read depth: full 117-line test and both helpers; full package gate, metadata and CI
workflow, relevant testing instructions and existing gate/metadata companions;
full shared architecture-testing export file and selected actual analyzer,
fact/policy value, projection and single-policy evaluation paths at the harness's
accepted clean checkout. No tests, imports, installers, Docker, database or provider
actions were executed for this documentation. No source or dependency changed.
