Source: [control-plane-kit-core/src/control_plane_kit_core/configuration.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Configuration artifact contract

This owner admits bounded text as an explicit container-file artifact, recording
content and source digests separately. The content digest hashes exact UTF-8
bytes; absent source digest defaults to it. The descriptor contains the content
even though repr suppresses it, and decoding recomputes the content digest.
Do not treat the descriptor as a redacted log record or the source digest as
proof of a trusted producer.

Targets must be normalized absolute file paths, excluding traversal and reserved
process/device/secret paths. File mode is a declared read-only choice. These pure
checks neither create a file nor prove its eventual mount provenance or mode.

Text/JSON/YAML/TOML have distinct parsing and secret-shaped-content checks.
YAML validation dynamically imports PyYAML and fails if unavailable. Structured
key scanning, password-URL and private-key markers are rejection policies, not
complete secret detection. Some parser/decoder exceptions preserve underlying
causes; do not claim all traceback paths are sanitized merely because the
outer message is categorical.

[configuration_rendering.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py)
is a selected producer; graph codec, diff and planning retain artifact meaning.
[test_configuration_artifacts.py](../../../../../control-plane-kit-core/tests/test_configuration_artifacts.py)
covers digest tampering, reserved targets, duplicate node artifact identities,
format rejection and explicit artifact/template revision diffs. Those are pure
contract laws; real file delivery and cleanup belong to the runtime interpreter.
