Source: [control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Typed configuration rendering

This is a pure representation interpreter from a ConfigurationTemplate and
explicit ConfigurationParameters to
[ConfigurationArtifact](../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py). It calls the
parameter object's configuration_values method, normalizes its output and
renders with Jinja StrictUndefined in an immutable sandbox. Globals/tests are
cleared; the only installed filter is deterministic JSON rendering. There is no
template file loader here.

The context language accepts scalar values, tuples and mappings with admissible
keys; lists, None and arbitrary objects are not its declared normalized values.
Nonfinite numbers and particular secret-shaped keys/strings reject. Source,
serialized context and streamed output have byte bounds. These bounds do not
constitute a total CPU/recursion limit or make an arbitrary caller-supplied
parameter method pure; keep the caller contract explicit.

Template source identity contributes to source_digest independently from output
content_digest. A template-comment revision can therefore change artifact
identity even if emitted bytes are identical. Preserve that distinction in
diff/planning. Output must still pass the artifact owner's media-specific
admission; successful Jinja rendering alone is insufficient.

Jinja2 and PyYAML are selected in
[pyproject.toml](../../../../../control-plane-kit-core/pyproject.toml).
Review the dependency version actually installed by the consumer before relying
on sandbox behavior. The
[configuration tests](../../../../../control-plane-kit-core/tests/test_configuration_artifacts.py)
exercise strict undefined/syntax errors, context rejection, deterministic output
and format/byte bounds. They do not establish process isolation or runtime file
delivery, and the artifact owner retains its own exception/disclosure behavior.
