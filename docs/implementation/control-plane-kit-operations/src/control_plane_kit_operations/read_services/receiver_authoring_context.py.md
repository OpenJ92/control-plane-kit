Source: [receiver_authoring_context.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/receiver_authoring_context.py).
Maintain this companion alongside its source.

`ReceiverAuthoringContextReadService.read(query, context=...)` requires an
operator with both scoped workspace-read and delegation-key-read before calling
its snapshot factory. The query names a workspace, optional complete five-pin
expectation, and optional exact live draft/head. The local snapshot protocols
contain no PostgreSQL or server dependency. The service owns one snapshot entry
and returns a detached immutable value with a fresh closed descriptor.

Each selected source retains its own graph/projection and complete bindings,
literal selected public configuration artifact, immutable introduction, and
recorded acceptance references. The Core selector validates the closed V2
configuration; the graph owner validates material/provenance. Current membership
and recorded first acceptance supply the factual label. No execution success,
provider health, admission permission, registry selection or retained retry is
inferred. Retired or incongruent sources refuse the entire context.

At most 64 source bindings, including duplicates, are returned. Logical query
and response JSON use sorted compact UTF-8 JSON with `ensure_ascii=False` and
`allow_nan=False`, capped at 16 KiB and 1 MiB. The database reader independently
owns pre-fetch transport bounds. All service failures become fresh categorical
errors without retained input or exception chains; no partial response escapes.

The direct service and framework-neutral HTTP/MCP mapping share this owner.
Existing redacted reads stay separate. Actual transport decoding, envelope
bounds and coordinated dependency adoption remain Servers #238.
