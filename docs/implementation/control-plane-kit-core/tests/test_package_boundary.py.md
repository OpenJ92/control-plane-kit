Source: [control-plane-kit-core/tests/test_package_boundary.py](../../../../control-plane-kit-core/tests/test_package_boundary.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file statically checks package metadata/dependency declarations, scans
Python AST imports for a selected forbidden set, and excludes selected
package-owned server names from Core source. The protected intent is a generic
pure Core with no product/process dependency.

Read [pyproject.toml](../pyproject.toml.md) and actual imports when the package
boundary changes. The scan is lexical and its forbidden set is explicit; it does
not comprehensively discover dynamic imports or establish effect-freedom for
all Python code. No container/provider behavior is exercised by these tests.
