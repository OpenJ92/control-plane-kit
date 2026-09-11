Source: [control-plane-kit-core/tests/test_adapter_parity_contract.py](../../../../control-plane-kit-core/tests/test_adapter_parity_contract.py).
Maintain this document alongside its source file. Recheck projection membership,
route/tool/schema mappings, descriptor expectations and rejection evidence when
changing read-adapter parity.

This 346-line suite contains five tests. It constructs Core HTTP/MCP contract
values and their read-projection bindings, using the operations facade and a
local descriptor assertion helper. It does not host either protocol, invoke a
tool, return a projection payload or compare live adapter responses.

## An explicit read projection catalogue

The first test compares every binding's operation ID, HTTP route ID, MCP tool
name and projection-schema name with an explicit ordered list of 37 rows. A
separate set assertion requires every binding's service role to be READS. The
literal table fixes membership, order and four-field associations; it is not
just a count or an expectation derived from the current enum.

Some names intentionally differ across the relation: read.activity-timeline
maps to read.activity/get_activity_timeline/ActivityTimelineReadResponse, while
read.open-sessions maps to read.sessions/list_open_sessions/OpenSessionsReadResponse.
The catalogue also covers graphs, saved drafts/revisions/preparations/attempts,
approvals, plans/runs/events, sessions, probes, authority records and secret
reference/provider metadata. Schema names are contract strings; their presence
does not execute a projection or establish that its actual response is redacted.

The actual [parity owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py)
builds these bindings from its read-definition tuple, fixes READS role and sorts
by operation ID. Its aggregate checks typed inputs, uniqueness of operation IDs,
HTTP route IDs and MCP tool names, route-role/schema agreement, and read-only
safety when the binding uses READS. It does not require every canonical projection
to be supplied or verify that an MCP tool is registered in a running server.

## Descriptor reconstruction

The second test pins three kind strings: adapter-parity-contract, nested
http-api-contract and nested mcp-streamable-http. It round-trips the computed
descriptor and rejects one extra outer key with InvalidAdapterParityContract.
This is mapping reconstruction, not fixed wire bytes/hash evidence or an
exhaustive malformed-key/type/nested-schema matrix. The test does not assert
concrete HTTP paths/methods/auth scopes, transport headers or response size limits.

Selected actual [HTTP code](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py)
constructs GET/READ/READ_ONLY routes owned by READS and resolves bindings by route
ID. The [MCP contract](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py)
describes endpoint/protocol/header/authentication requirements. These are source
context for the fixture, not live transport evidence supplied by this suite.

## Schema, role and duplicate negatives

The third test constructs two singleton read.workspace bindings: one keeps READS
but changes the projection schema to WrongSchema; the other uses PLANNING with
the correct WorkspaceReadResponse schema. Both must raise
InvalidAdapterParityContract during aggregate construction. The actual route
declares READS and WorkspaceReadResponse, so each changes one relevant agreement.
Singleton catalogues are permitted here; missing the other 36 projections is not
the rejection. There is no independent negative changing a read route's safety,
an unknown route ID or an unregistered MCP name.

The fourth test repeats the same read.workspace binding twice. This duplicates
operation ID, route ID and tool name simultaneously. The owner checks operation
ID first, then route and tool names, so this is one collision example rather
than three isolated uniqueness tests. Only the exception class is asserted;
message text and rejection precedence are not test expectations.

## Disclosure assertions and their limits

The final test renders a benign canonical descriptor and excludes fastapi,
uvicorn, dockerfile and private_projection from its lowercase repr. It also
uses the full [shared assertion helper](../../../../control-plane-kit-core/tests/contract_security_assertions.py),
which recursively checks mappings and non-string/non-bytes sequences for fixed
normalized raw-secret key names, then excludes five known value markers from
lowercase repr. This is not a source import audit or proof that arbitrary
transport-private or secret-bearing values cannot enter a projection.

No secret canary is injected and no real response is inspected. No test checks
descriptor size/depth, diagnostic length, exception repr or cause/context chains.
Selected parity decoders wrap ValueErrors with their text and cause; the suite
does not establish universal redaction or cause-free public errors. Its private-
state test name should not expand the limited fixed-word/key/marker assertions.

## Ownership and maintenance

The tested structure is a named relation between shared projection schemas,
HTTP route metadata and MCP tool names. Successful value construction proves
the selected local compatibility rules, not that both adapters call the same
service, enforce authentication or produce equivalent runtime responses.

Authoring read all 346 lines and five tests, retained the full shared assertion
helper, and read selected actual parity binding/aggregate/factory/definition,
HTTP route construction/lookup and MCP contract fields/guards. Neither the
1,407-line parity owner nor the HTTP/MCP owners gain coverage from this packet.
No source changes, application imports, executable tests, providers or live
actions occurred. Documentation links, whitespace and the frozen source/test
guard were checked; these checks are not executable adapter evidence.
