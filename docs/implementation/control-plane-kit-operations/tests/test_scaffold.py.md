Source: [control-plane-kit-operations/tests/test_scaffold.py](../../../../control-plane-kit-operations/tests/test_scaffold.py).
Maintain this document alongside its source. Recheck the package exports,
boundary declaration and test runner when changing the import contract.

This 19-line unittest file contains one package-import smoke test. It calls
importlib.import_module("control_plane_kit_operations"), then asserts the public
__version__ is "0.1.0" and OPERATIONS_PACKAGE_BOUNDARY.import_package is the
same package name. The main guard permits direct unittest invocation.

The test name says imports_after_core, but the method does not explicitly import
Core first, install either distribution, start a new interpreter or clear the
module cache. In an already populated test process, import_module may return the
cached package. The assertion therefore proves those two accessible values in
that process, not import-order independence or a fresh isolated installation.
It has no fixtures or database/provider calls of its own; importing the package
still traverses whatever uncached imports its current root requires.

The actual [package root](../src/control_plane_kit_operations/__init__.py.md)
imports DeploymentProgramStage from Core, imports the boundary from foundation,
and defines __version__ directly. The
[foundation owner](../src/control_plane_kit_operations/foundation.py.md) constructs
a frozen OperationsPackageBoundary whose import_package is the asserted string.
The test does not check the boundary's distribution, depends_on, deployment spine,
owner lists, descriptor, exact type or complete export surface. Those declarations
must not be inferred to have been exhaustively validated by this smoke test.

The inspected [pyproject.toml](../../../../control-plane-kit-operations/pyproject.toml)
declares version 0.1.0 and Core plus psycopg dependencies. The test does not compare
installed distribution metadata with the root version, validate dependency
versions or prove wheel contents. The
[runner](../../../../control-plane-kit-operations/test.sh) supplies the broader
context: its suite command installs Core then Operations and discovers tests.
A separate clean-container command imports Operations from /tmp, checks its
version and checks that the architecture-testing package cannot be found.
That isolation check belongs to the runner, not to this method.

Read depth: full test, full foundation owner and full pyproject; selected actual
root imports/version and runner suite, compile and clean-import commands. No
full transitive-import audit is claimed. This documentation pass performed no
application imports, tests, installs, containers, database/provider operations
or source changes. It introduces no security or runtime mutation surface.
