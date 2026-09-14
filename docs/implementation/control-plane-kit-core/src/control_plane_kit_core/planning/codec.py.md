Source: [control-plane-kit-core/src/control_plane_kit_core/planning/codec.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Activity-plan representation boundary

ActivityPlanDescriptorCodec owns the version-one, schema-tagged representation
of the [activity language](activity_plan.py.md). It serializes identities,
operations/targets, dependencies, risk/impact and canonical compensation. It
does not persist a plan or prove that any target exists or action was approved.

Full plan decoding checks schema/version, exact top-level and activity fields,
typed variants and composition, then requires re-encoding to equal the JSON
representation of the input. This last comparison rejects ignored nested
fields, noncanonical ordering and other lossy encodings. The version must be
an actual integer. Missing compensation is not silently upgraded: its encoded
operation and material source must equal the value derived from the forward
operation.

dumps uses compact, sorted-key standard-library JSON. It is deterministic for
the supported plan values; it is not the separately owned runtime-intent
canonicalization/fingerprint contract. ActivityPlan normalizes typed values
before encoding, while descriptor decoding requires their canonical ordering.
Do not generalize JSON's tuple normalization to the activities/dependencies
inputs, which are explicitly required to be lists.

The exported single-operation helpers are a narrower boundary: they call the
operation encoder/decoder directly. They do not apply the full plan envelope,
DAG checks, canonical compensation comparison, re-encoding equality or the
full decoder's exception wrapping. In particular, standalone operation decode
can ignore extra fields that full plan decoding would reject. Consumers needing
strict lossless operation admission must establish that at their actual owner.

Review operations carry a subject and reason, not the graph diff's before/after
values. That avoids copying changed material into a review instruction, but
arbitrary target names and subject keys are not universally scrubbed. Error
messages may include unknown variants or chained constructor text, and no
whole-descriptor byte/depth bound is added here.

[test_activity_plan_codec.py](../../../tests/test_activity_plan_codec.py.md)
covers selected round-trip, canonicalization and rejection laws.
[Compensation tests](../../../../../../control-plane-kit-core/tests/test_compensation_planning.py)
protect the required inverse/source representation. Neither establishes storage
transactionality or provider acceptance.
