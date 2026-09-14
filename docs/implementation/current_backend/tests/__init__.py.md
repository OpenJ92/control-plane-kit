Source: [current_backend/tests/__init__.py](../../../../current_backend/tests/__init__.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This docstring-only package marker makes the test directory an importable package
for the [runner's](../runner.py.md) unittest discovery with an explicit repository
top level. It contains no fixture setup, test registration, imports or runtime
effects. Shared fixtures remain owned by their individual test modules, including
[test_contracts](test_contracts.py.md); do not move orchestration or hidden setup
into this marker simply because every package import reaches it.
