Source: [control-plane-kit-operations/tests/test_execution_lease_recovery_read_projection.py](../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_read_projection.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three tests protect conditional typed recovery evidence in the existing
event descriptor and InstanceReadService.run_events projection. They construct
actual records and page values over tiny store doubles; no database, recovery
command, provider, credential authentication or HTTP/MCP transport runs here.
The imported private _event_descriptor is called directly as public_event_descriptor;
the alias does not create a separately supported public API.

_WorkspaceStore returns workspace-a or raises KeyError for another ID. The execution
double always returns the same FAILED, started, unsettled run and CLAIMED request,
ignoring its requested run/request arguments. The request carries a synthetic
worker-b generation-eight claim and fixed approval/idempotency metadata. event_page
returns one supplied event through actual ReadPage.from_candidates, with an ordinal
cursor derived from that event and the caller's scope. The fake does not perform
SQL filtering, cross-workspace lookup or durable history reconstruction.

The first test compares the complete eight-key descriptor of an ordinary RUN_OPENED
event, including payload={}, failure=None and no recovery key. This protects the
existing ordinary-event shape. It then builds takeover evidence from worker-a/seven
to worker-b/eight, requires a recovery key and compares that nested descriptor to
an explicit complete dictionary. It does not compare the entire recovery-bearing
outer descriptor to an independent expected dictionary.

The second test supplies the same takeover event to InstanceReadService using the
workspace/execution doubles and an object() graph store. A RUN_EVENTS request for
workspace-a/run-a with limit10 must return the same page request, exactly one item,
and recovery equal to the actual evidence object's descriptor. This checks facade
wiring to the projection, not an independent second implementation of the evidence
descriptor. It does not assert next_cursor, truncated, SQL call counts or rejection
of an incorrect collection, scope or foreign run.

The final test constructs retry-as-new-run evidence with the same worker-a/seven
fence on both sides. Both direct event projection and the service page must expose
the explicit expected nested descriptor. The page request is checked; unlike the
takeover service test, this method accesses its first item without a separate exact
item-count assertion. No retry service created the represented event or successor.
The execution double still supplies its unrelated worker-b/eight claim: these
tests do not bind historical evidence to the current claim or prove valid recovery
admission/replay. They project the supplied event value.

The takeover and retry outputs selected by these tests are JSON-rendered and must
omit eight fixed substrings:
authority_reference, scopes, claimed_at, lease_expires_at, idempotency, fingerprint,
secret and endpoint. This demonstrates absence of those strings from the chosen
representations. The events carry no hostile secret payload, failure text or
authority reference, and ordinary evidence is empty. Although the service fake
contains claim/idempotency metadata, this is not an exhaustive redaction test,
value-based secret detector or proof that arbitrary strings embedded in IDs are
safe. Full result repr and arbitrary serializers are not inspected.

The actual [history projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py)
checks the collection, requires the workspace, loads the run and its admitted
request, verifies the request's workspace, then maps the store page. The
[facade](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/instance.py)
delegates run_events to that owner. Those source checks are not all exercised as
negative cases in this file; forgiving fake lookups cannot establish database
identity containment or authentication. The graph store sentinel only supports
this narrow history path, not graph-read behavior.

The event descriptor conditionally adds event.recovery.descriptor() when recovery
is non-None. General evidence/failure details use the shared redaction helper;
the typed recovery descriptor is added directly. Intrinsic
[record laws](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
govern recovery/event shape. These tests cover ordinary, takeover and retry forms,
not every renewal/abandonment variant, malformed-record subclass, failure projection
or maximum-length value. Historical run and worker coordinates are intentionally
visible; the projection neither grants their authority nor verifies provider state.

Actual [page values](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
preserve request/cursor/truncation metadata through map. The single-candidate fixture
does not exercise multi-page ordering, cursor continuation, output-byte bounds or
large collections. Those contracts and protocol authorization/parity belong to
their existing owners rather than being re-created here.

Read depth: full274 source, all three tests and store helpers; selected actual
history run_events/event/failure descriptors, facade wiring and page construction/
map, full shared redactor, with retained recovery evidence/event/claim contracts.
No whole history/facade or HTTP/MCP suite audit is claimed. Local links, whitespace
and source guards were checked. No executable tests/imports, database setup,
credentials, provider/runtime actions, source changes, staging or publication
occurred during authoring. This companion adds no security surface or permission
to perform recovery, retry or live resource mutation.
