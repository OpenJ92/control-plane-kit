Source: [control-plane-kit-core/src/control_plane_kit_core/_run_identity.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This private predicate centralizes the run-identity grammar for the public
[RunId owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py),
[journal values](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py) and
[secret grants](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py). It admits exact built-in
strings of 1–200 ASCII characters with an alphanumeric start and `._:-`
allowed thereafter. It does not mint IDs, persist runs or infer optionality.

The journal/grant string boundaries share the grammar without importing a
durable store or creating another RunId class. The
[run-identity tests](../../../../../control-plane-kit-core/tests/test_run_identity.py)
protect this agreement, exact nominal identity, import-order independence and
candidate-free errors at public consumers. Error ownership remains with those
consumers; the predicate itself just returns a boolean.
