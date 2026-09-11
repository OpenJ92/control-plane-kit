Source: [control-plane-kit-operations/src/control_plane_kit_operations/read_services/errors.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/errors.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

`ReadModelError` is the canonical `ValueError` subtype for a projection that cannot construct the requested read model from durable truth. It has no custom constructor, fixed message, size cap, cause clearing, redaction, retry policy or HTTP status. Those choices belong to the raising projection and public adapter. Do not assume arbitrary instances are safe public error bodies.

The [read-services facade](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/__init__.py) exposes this same class; [package tests](../../../../../../control-plane-kit-operations/tests/test_read_services_package.py) check canonical identity rather than a duplicate compatibility exception. This dependency-free leaf owns no store, effect or transaction. Full five-line owner read; no execution performed.
