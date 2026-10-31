Source: [configuration.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py).
Maintain this companion with source and imported contract changes.

`ConfigurationArtifactRequirement` is an immutable ordered four-field declaration:
artifact ID, target path, media type and mode. It has no payload, source/content
digest or implicit default. Shared slot validation uses the existing artifact
grammar and enum/path restrictions without constructing a dummy artifact. The
closed descriptor revalidates nominal values and detaches decoder input errors.

`ConfigurationArtifact` remains the bounded secret-free concrete file language;
its content parsing, digest verification and wire shape remain unchanged.
Requirement admission by products and declared receiver selectors rejects forged
or non-nominal values before virtual descriptor dispatch. Neither value performs
I/O or grants runtime authority.
