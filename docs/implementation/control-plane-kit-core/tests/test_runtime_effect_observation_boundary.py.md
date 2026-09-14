Source: [control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py](../../../../control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Existing result and package boundary laws

This file protects the seam between
[runtime_effects.py](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py) and
[the observation language](../src/control_plane_kit_core/runtime_effect_observation.py.md):
matching request/event identities, hidden transient grants, live-result
canonicalization and exact result/endpoint shapes. Unlike observation evidence,
live-result evidence follows its own JSON validation; these tests must not be
read as a universal redaction guarantee.

Endpoint tests exercise bounded identities/evidence and reject hostile nested
subclasses or protocol dispatch before arbitrary behavior can run. Preserve
endpoint ordering and the full-result byte ceiling when changing fingerprints.
The “Operations bounds” tests protect compatible Core values; they do not
execute an Operations database write.

Package checks verify public export identity, scan imports/calls for prohibited
effects and inspect an inventory selected by `CPK_PACKAGE_MODULE_INVENTORY`
(or the repository fallback). The inventory assertion expects a fixed historical
dependency list, while current source also imports runtime authority and
verification. The fallback
[package-module-inventory.json](../../../../docs/architecture/package-module-inventory.json)
labels itself historical. Consequently, a passing inventory assertion must
not be advertised as a complete current import inventory. This discrepancy is
recorded under [#1801](https://github.com/OpenJ92/control-plane-kit/issues/1801);
this documentation change does not alter the test or resolve the inventory policy.

The AST import/call scan is useful static boundary evidence, not an exhaustive
proof that all future execution is effect-free. No suite was executed for this
documentation batch.
