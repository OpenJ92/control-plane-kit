Source: [control-plane-kit-core/tests/test_milestone_closeout.py](../../../../control-plane-kit-core/tests/test_milestone_closeout.py).
Maintain this document alongside its source file. When module inventory, discovery scope, import restrictions or assertion limits change, verify and update this companion in the same change.

This 135-line file has three filesystem/AST tests. They guard the Core source-file
inventory, selected imports and a selected test-framework restriction. They do not
run a roadmap milestone, interpret an activity plan, inspect live resources or
establish product acceptance. The AST here is Python's parsed source syntax used
to inspect imports; it is not a deployment plan being executed.

## Exact source-file names

PACKAGE_ROOT is derived from the resolved test-file location; SRC_ROOT is its
`src/control_plane_kit_core` directory. The first test recursively discovers every
`*.py` file under that root. `_module_name` removes the suffix, takes the relative
path and joins its components with dots. Thus package initializers remain names
such as `operations.__init__`, rather than being normalized to their importable
package name.

The resulting set must equal the literal EXPECTED_MODULES set, covering root,
operations, planning and topology files. An unexpected Python file is as much a
mismatch as a missing expected file. This is filesystem discovery, not a git index,
installed-wheel inspection, symbol inventory or the architecture JSON inventory.
It has no explicit exclusion for untracked/generated Python files in that root.
The equality establishes names only; it does not read their behavior or establish
that each file imports successfully. Authoring inspected the current filename
listing as context without executing this assertion.

## Import roots and their interpretation

`_import_roots` reads a file as UTF-8, parses it with `ast.parse` and walks all
syntax nodes. Normal imports contribute each alias's original first name
component. From-imports contribute the first component of node.module when
present. A relative from-import with no module text contributes
control_plane_kit_core; a relative import with module text is treated by that
text's first component, not resolved to an absolute package coordinate.

The source-import test inspects every discovered Core Python file in sorted order
and intersects those roots with eight forbidden names: control_plane_kit, docker,
fastapi, httpx, mcp, psycopg, pytest and uvicorn. Findings include the module name
and sorted forbidden roots; the final assertion requires an empty list. Both
normal/from imports and imports nested under functions or conditional blocks are
visible, whether or not those branches execute. Invalid syntax or unreadable
files fail the test rather than becoming an empty finding list.

This catches static imports rooted at those exact names, including submodules and
aliases. It does not resolve dynamic imports, imported symbols' dependencies or
all relative imports. Operations/interpreter package names, requests and socket
are not in this particular forbidden set. Absence of these eight roots is not a
complete acyclic dependency graph, proof of purity or universal prohibition on
runtime/product dependencies. Other ownership rules and tests retain their own
scope.

## The framework check is specifically about pytest

The third test recursively scans only `test_*.py` under the package tests directory
and uses the same AST helper. It records relative filenames whose import roots
contain pytest and expects none. Despite its name, it does not require a unittest
import or reject every other framework. Helper/fixture files without that filename
pattern are outside this scan, and dynamically imported frameworks are not found.

The package [test harness](../../../../control-plane-kit-core/test.sh)
uses unittest discovery as one stage among integrity, compilation and installed
import checks. This file's three tests are only part of that larger gate. They
neither execute those other stages nor validate their configuration. The harness
was read for context and was not run for this documentation work.

The [surface-result test](../../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py)
separately checks that this source text contains the quoted surface-result module
name. That small guard is not equivalent to executing the exact-inventory test or
proving the module belongs to every package/roadmap inventory.

Authoring read the full file, full harness, source filename listing and actual
surface-result consumer assertion. No Core module imports, AST test execution,
unittest discovery, installation, Docker or provider actions occurred. Only this
test's row gains coverage. Source-file additions or removals should update the
literal inventory deliberately; changes to allowed imports require a separate
ownership judgment, not merely silencing a failing gate. No new runtime, security,
network or mutation surface is introduced by this companion.
