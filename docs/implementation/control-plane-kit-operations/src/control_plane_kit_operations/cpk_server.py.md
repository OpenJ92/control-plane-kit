Source: [cpk_server.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py).
Maintain this companion alongside its source.

#2088 maps one canonical optional `http_verification_intent` at both direct plan
and composed prepare entrances. Absence is preserved; present null/unknown/raw
profile override refuses with bounded 400 before the service call. The mapper
produces the typed intent and leaves profile/history selection to Operations.
Servers client/journal and public composition qualification remain separate.

#2041 treats only the canonical `read.workspaces`/READS pair as an inventory
entrance. It requires an operator and explicitly selected exact durable-only
resolver; static/intersection/unsupported compositions return 503. Each public
entrance constructs a new internal reader using the resolver's installation and
factory, discarding prior carrier authority. Closed limit/after arguments pass
to the dedicated owner without constructing a fake workspace context. HTTP/MCP
share results and fixed errors. Storage is entered only by that reader.

#2078's advancement route explicitly calls the owner's diagnostic entry once.
It accepts exact success/failure types and matches the closed failure correlation
against the admitted command. The fixed service-error response may carry only
that diagnostic, capped at 8 KiB of serialized UTF-8 including outer fields and
escaping. Missing/unbound/oversized evidence retains the generic error response;
auth refusal and successful advancement projection are unchanged. The response
does not prove acceptance or authorize retry. The focused owning gate at
`5558ea434ddd0cc05dee238c5df1356a67c8691b` passed all 19 selected tests and the
integrity, compilation and clean import phases. Full CI and integration remain
separate; see [the terminal record](https://github.com/OpenJ92/control-plane-kit/issues/2078#issuecomment-6594537852).

`command.desired-graph.set` accepts optional `explicit_secret_choices`, mapping
node IDs to arrays of the existing Core selection descriptor
`{reference_id, intent, version_id}`. Unselected, duplicate, malformed, oversized
and unknown-node values refuse before dispatch. Authenticated context supplies
`actor_scopes`; a body authority claim cannot grant permission. HTTP and MCP use
this same translation. The injected DesiredGraphCommandService owns metadata
preparation, current fences, atomic writes and original replay, not this route.

Operations adapters map authenticated HTTP/MCP protocol values to their owning
application commands. `command.desired-graph.set` and inline
`command.deployment.prepare` forward optional `proposed_graph_id` without
generating or rewriting graph material. Typed commands own validation and
fingerprints; the existing service owns authority, persistence and replay.
Saved preparation with a proposal is malformed rather than silently ignored.
Adapter tests prove both HTTP and MCP forwarding; durable behavior belongs to
the PostgreSQL planning and preparation tests.

The workload verifier read adds a closed HTTP path / MCP argument mapping for
workspace_id and purposes. Its bounded CSV is decoded to existing purpose enum
values without hiding duplicates; the projection owns allowed-family semantics.
Workspace-scoped DELEGATION_KEY_READ is checked before opening the existing
read UoW. No command or effect surface is added.

The receiver-authoring-context route requires both workspace-read and
delegation-key-read before UoW construction, then validates its closed logical
arguments. Its dedicated service owns one read-only repeatable-read snapshot;
the ordinary read UoW is not also entered. HTTP-shaped path plus optional
expected/pending-draft values and MCP-shaped complete arguments share the same
query/service/descriptor. Database and decode failures map to bounded fresh
application errors. This mapping does not implement Server query decoding or
authorize dependency adoption; those remain Servers #238.

Authorization remains on its closed route-family interpreter even when neither
workspace nor provider-policy administration is configured. The interpreter
returns the existing fixed family-unavailable 404 before any workspace context,
unit of work or family owner is acquired. Generic unsupported-role handling is
therefore limited to roles that do not already have a closed interpreter;
configured authorization families and dynamic principal admission are unchanged.
