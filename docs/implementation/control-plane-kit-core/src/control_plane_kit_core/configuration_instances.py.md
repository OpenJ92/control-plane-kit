# Exact configuration instances

Governing issue: [#1918](https://github.com/OpenJ92/control-plane-kit/issues/1918),
part A of [#1886](https://github.com/OpenJ92/control-plane-kit/issues/1886).

`configuration_instances.py` owns pure immutable allocation references, ordinary
selections and total cleanup outcomes. A reference names one allocation and its
workspace/runtime/node, artifact slot, target path, media type, file mode and
content digest. It carries no contents, provider locator, credential, generated
identity or mutable current-use flag. Equality means exact public coordinates,
not proof of ownership or actual installed material.

An ordinary selection has 1–32 references in one scope, with unique allocation,
artifact and path identities. Cleanup also has 1–32 same-scope references and
unique allocation identities, but deliberately permits several incarnations of
one artifact/path. Duplicate identities are rejected before canonical sorting.

```python
from dataclasses import replace
from control_plane_kit_core.configuration import ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef, ConfigurationInstanceSelection,
)
from control_plane_kit_core.planning import CleanupConfigurationInstances

old = ConfigurationInstanceRef(
    allocation_id="allocation-a", workspace_id="workspace-a", runtime_id="docker",
    node_id="api", artifact_id="service-config", target_path="/etc/service/config.json",
    media_type=ConfigurationMediaType.JSON, file_mode=ConfigurationFileMode.READ_ONLY,
    content_digest="a" * 64,
)
new = replace(old, allocation_id="allocation-b", content_digest="b" * 64)
selected = ConfigurationInstanceSelection((new,))
cleanup = CleanupConfigurationInstances((old,))
```

The example constructs values only. Selecting `new` neither allocates it nor
proves that `old` is unused. Planning owns the cleanup operation and requires
DESTRUCTIVE impact, HIGH or CRITICAL risk and no automatic compensation. The
compiler does not emit cleanup.

The three profiled codecs reject unknown/extra/missing/null fields, wrong
primitive types and invalid status/reason pairs. Canonical bytes use RFC 8785,
a 65536-byte ceiling, duplicate-key/nonfinite refusal and exact re-encoding.
Errors are bounded and do not include the supplied descriptor or parser text.

## Effect and history boundary

`runtime_effects.py` owns the new `configuration-activity.v1` capability. Start
and reconcile require an exact selection matching every artifact in the sole
selected target product. Cleanup has no products, process-authority deliveries
or duplicate selection field; its operation owns all candidates. Transport
authority and transient grant rules retain their existing meanings. Older
`realize-activity` requests cannot carry these references or cleanup operations.
Absent optional fields remain absent from historical descriptors and preimages.

`configuration_cleanup_result(request, outcomes)` derives the original effect
identity and aggregate: all removed/absent succeeds, any unknown is uncertain,
and other combinations fail. `configuration_cleanup_outcomes(request, result)`
requires every original full ref exactly once, the fixed aggregate/failure,
empty observations and the exact profiled evidence. Unsupported is an outer
outcome-free capability refusal, never an empty successful candidate set.
These checks conserve reported structure; they do not verify provider truth.

The existing full-result fingerprint and durable outcome preimage limit remains
8192 bytes. Intent and request construction conservatively reserve each full
candidate's longest legal outcome row, all three actual result envelopes and
the maximum escaped event identity. Factory and reader also check actual size.
This reuses the actual result constructor, not a parallel envelope or estimated
fixed overhead. Oversized cleanup syntax stays representable but cannot become
an effect contract. Later planning must obtain approval for separately fitting
batches; dispatch cannot silently split or truncate an approved request.

Operations preserves the optional selection in its strict original-intent
codec. Admission refuses cleanup before clock/ID allocation or writes, and
translation refuses it before graph/material interpretation. There is no new
store, schema, allocation producer, reservation, dispatcher or provider effect.

## Evidence and handoff

The owning Core tests are `test_configuration_instances.py`,
`test_configuration_cleanup_contract.py` and `test_configuration_effect_contract.py`;
historical literal intent goldens remain unchanged. Operations tests exercise
strict intent preservation, the actual durable outcome record/input encoder,
and both explicit refusal boundaries. Existing exact codec import/call policies
are extended only for the new selection codec and retain equality checks.

Part B owns provenance and accepted current use; C owns exact cleanup plans and
approval; D owns complete reservation/participant protocol and folding. Provider
ownership, fresh non-use checks, deletion and truthful uncertainty remain with
the interpreter. No A test or pure value grants that later authority.
