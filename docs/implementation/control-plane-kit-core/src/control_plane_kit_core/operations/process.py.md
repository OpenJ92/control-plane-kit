Source: [control-plane-kit-core/src/control_plane_kit_core/operations/process.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/process.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Process health and shutdown as contract data

`ControlPlaneProcessContract` composes health endpoint declarations, readiness
dependencies, verification, observation, shutdown and optional HTTP/MCP contracts.
It is a handoff value, not a hosted process: it registers no routes, reads no
health, authenticates nobody and shuts down no resources.

`HttpStatusProbeContract` requires a typed liveness/readiness kind and actual
Boolean disclosure flags. Liveness must be public without declared sensitive
state; readiness must be private. Factories use `/health/live` and `/health/ready`.
Status tuples must be sorted, unique integer HTTP codes, but an empty tuple is
admitted. The response-byte limit is 1..65536, default 1024. Paths must start
with slash, differ from `/`, and exclude query, fragment and whitespace; they
have no length cap or general URL/path-normalization guarantee here. This module
does not measure a response or enforce a network disclosure rule.

Readiness dependencies name six possible kinds and a Boolean required flag.
The evidence key defaults from kind for any falsy constructor input; otherwise
it must use a restricted ASCII text alphabet and exclude three sensitive-word
substrings. Its length is not capped. The combined contract rejects duplicate
kinds and normalizes enum order; it does not require all six kinds or unique
evidence keys, and required=False is valid data.

The combined value checks health kinds and distinct endpoint paths, plus typed
verification/observation/shutdown values. An HTTP or MCP dependency requires its
matching contract even if that dependency is marked optional. Without the
corresponding dependency, direct construction does not validate a non-None
http_api/mcp value; descriptor decoding does reconstruct those optional types.
These compatibility checks do not prove endpoints are installed or usable.

The observation contract fixes append-only projection and preservation of
desired graph truth, with a declared evidence limit of 1..65536 bytes. Shutdown
fixes retained-data preservation and observation recording, and accepts a
non-Boolean numeric timeout in (0,300], normalized to float. Its chained range
condition rejects NaN/infinities. No payload is measured, event appended,
deadline scheduled or cleanup performed by these values.

Descriptors include all nested contracts and a fixed process-contract kind.
Decoders require exact field sets and their mapping/list/text/Boolean/number
shapes, then rerun construction checks. They do not establish a universal
aggregate-size, safe-error or redaction boundary: nested caller text and wrapped
exception causes can remain visible.

Full 531-line owner and full 155-line
[governing test](../../../../../../control-plane-kit-core/tests/test_process_operational_contract.py)
read. Selected actual [HTTP](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py),
[MCP](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py)
and [entrypoint handoff](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py)
contracts, previously read verification contracts and the complete test security
helper supplied context. This is not a full hosted-server, dependency or
operational audit. No executable validation, network exposure, retained-data
change or runtime action was performed.
