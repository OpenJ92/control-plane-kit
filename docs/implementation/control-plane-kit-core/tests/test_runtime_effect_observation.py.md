Source: [control-plane-kit-core/tests/test_runtime_effect_observation.py](../../../../control-plane-kit-core/tests/test_runtime_effect_observation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Observation language law navigation

This file exercises the request and six-way result sum in
[runtime_effect_observation.py](../src/control_plane_kit_core/runtime_effect_observation.py.md).
Request tests distinguish retained intent identity from generated effect
correlation and transient authority. Two fresh complete TLS grant sets produce
the same public intent/descriptor while remaining different hidden request
material; this is an omission law, not evidence that credentials are
interchangeable. HTTP expected-body digests remain part of retained intent.

Grant fixtures cover allowed delivery, pull, verification and admitted TLS
connection uses, rejecting duplicate/unrelated uses and wrong coordinates.
They test the grant-validation boundary using constructed values; they do not
contact a secret provider or establish every external authorization check.

Result tests cover the exact six variants, required/forbidden failure and
endpoint combinations, bounded nonempty exact JSON evidence, defensive copying,
redacted categorical errors, canonical fingerprints and the complete 8 KiB
ceiling. Preserve the alias-mutation negative cases when refactoring evidence
storage: a frozen outer dataclass alone would not protect nested caller values.

Read [the existing-boundary tests](test_runtime_effect_observation_boundary.py.md)
for live-result fingerprinting and package ownership. Neither file proves
runtime success or durable reconciliation. This note records existing test
intent, without fresh executable evidence.
