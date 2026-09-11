Source: [control-plane-kit-core/test.sh](../../../control-plane-kit-core/test.sh).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This is the package's Docker-backed validation entrypoint. It runs shared
test-support self-tests, package integrity, installed-package unittest discovery,
byte compilation and an installed-root import/version check in separate
containers. The shell uses set -eu; each docker run must succeed before the next
stage begins. Success from an early stage is not completion of the whole script.

The repository/package mounts are read-only; package installation operates on a
copy under /tmp inside the test container. The script selects CPK_CORE_TEST_IMAGE
or a mutable python:3.14-slim default. pip installs declare network/dependency
work inside test containers; the script does not establish immutable image or
wheel provenance. Containers use --rm, without proving cleanup after every
possible external interruption.

Contract-bearing dependencies are the shared
[package integrity checker](../../../test_support/package_integrity.py),
[package metadata](../../../control-plane-kit-core/pyproject.toml) and the
historical [module inventory](../../../docs/architecture/package-module-inventory.json)
mounted at an explicit environment-selected path for package tests. Inventory
assertions retain their historical limits.

This harness is executable validation with container effects. Reading this
companion does not authorize running it during a docs-only or provider-held
task. Evidence needs the owning invocation's complete output/exit status;
neither source inspection nor compileall alone demonstrates package success.
