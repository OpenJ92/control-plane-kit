Source: [control-plane-kit-core/tests/test_configuration_artifacts.py](../../../../control-plane-kit-core/tests/test_configuration_artifacts.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file joins
[artifact admission](../src/control_plane_kit_core/configuration.py.md) with
[template rendering](../src/control_plane_kit_core/configuration_rendering.py.md),
graph codec and diff. It protects exact content digests, unsafe target/content
rejection, node-level duplicate identities/targets and tamper rejection.

The two-digest law is especially useful: changing template definition can change
source_digest while content and content_digest stay equal. Keep that revision
visible to graph diff. ProxyConfiguration is an explicit pure parameter
fixture; it is not an application configuration registry.

Rendering tests cover deterministic JSON, strict undefined/syntax failures,
closed context rejection and bounded format-validated output. They do not prove
general sandbox resource limits, comprehensive secret detection, all traceback
redaction or actual container file permissions. No tests were run for this
documentation batch.
