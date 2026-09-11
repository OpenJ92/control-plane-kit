Source: [current_backend/__init__.py](../../../current_backend/__init__.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This package entrance forwards the selected source-lock values and operations
through explicit imports and `__all__`. Their contracts belong to
[source_lock.py](source_lock.py.md), not to a second implementation here.
It does not export the static contract validator or executable gate runner.
When changing the facade, inspect callers and the referenced owner; exporting a
function that can fetch/materialize source does not mean importing this package
invokes that function or authorizes its effects.
