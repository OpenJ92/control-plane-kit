Source: [control-plane-kit-core/tests/test_scaffold.py](../../../../control-plane-kit-core/tests/test_scaffold.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This is the small installed/importable package witness: importing
control_plane_kit_core exposes version 0.1.0 and does not bind the frozen
control_plane_kit package name in its module dictionary. It supports the
extraction boundary without proving every transitive import is clean.

The owning [test.sh](../test.sh.md) also performs an installed-package import
outside the source checkout. Keep this fixture's expected version consistent
with an intentional package-version change; it is not a deployment or whole
suite acceptance test.
