Source: [control-plane-kit-core/tests/test_http_api_contract.py](../../../../control-plane-kit-core/tests/test_http_api_contract.py).
Maintain this document alongside its source file. Recheck route inventory,
descriptor fields, ordering, admission negatives and evidence limits when the
HTTP contract changes.

This 250-line suite contains seven tests of pure HTTP route/schema/error values.
It imports the Core operations facade and unittest, with no local fixture helper,
HTTP client, ASGI app or running service. Route descriptors specify an interface;
these tests do not serve requests, authenticate callers or inspect response bytes.

## Literal route inventory and ordering

The first test wraps operator_read_http_routes in HttpApiContract and compares
all method/path pairs with an explicit ordered list of 37 GET routes. Separate
set assertions require every route to use READS service role and READ_ONLY safety.
The paths cover workspace/graph/overview/control surface, authority and secret
metadata, drafts and saved revision histories, probes, approvals, plans, runs
and session histories. This assertion fixes literal paths and order; it does not
directly fix every route ID, auth scope or response-schema name.

The third test reverses the factory input, constructs a contract and compares the
descriptor's path list with the same explicit expected order. It fixes the outer
http-api-contract kind, round-trips the mapping and rejects one extra outer key.
The actual [HTTP owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py)
sorts by path template, then method value, then route ID. These read-only examples
demonstrate path ordering; they do not isolate method or ID tie-breaking with
otherwise equal paths. There is no independent canonical byte/hash assertion or
exhaustive missing-key/type/nested-schema matrix.

## One complete command descriptor

The second test constructs planning.create-plan as POST on the workspace plans
path, using PLANNING service, PLAN_WRITE scope and COMMAND safety. It compares
the entire route descriptor with a literal mapping: route ID, method/path,
service/scope/safety, request PlanTransitionRequest and response
PlanPreparedResponse both declaring max_bytes 65536, and the default error
contract. The latter fixes statuses 400, 401, 403, 404, 409, 422 and 503 plus
BoundedError with max_bytes 8192. The route mapping must round-trip.

Those exact numeric declarations are tested. No payload of either size is
accepted or rejected and no server-side limit is exercised. The actual schema
constructor additionally requires an exact integer between 1 and 1048576, but
this suite contains no minimum/maximum/over-limit or boolean-size negative.
The error schema's name does not demonstrate bounded or redacted error content.

## Identity, path and duplicate negatives

The fourth test rejects a route ID containing a space, a path containing the
query suffix ?debug=true, and a contract containing the first factory route
twice. The actual first factory route is read.workspace. Repeating it duplicates
both route ID and method/path pair; the owner checks route-ID uniqueness first.
This is not an isolated method/path collision test, and the test asserts neither
diagnostic text nor rejection precedence.

The actual identity guard permits nonempty ASCII letters, digits, dots, dashes
and underscores. The path guard requires an absolute non-root path without
query, fragment or whitespace. Only the space-containing ID and query-containing
path are negative examples here; the other guard branches and template grammar
are not comprehensively tested.

## Safety, scope and error-contract negatives

The fifth test rejects a READS/READ/READ_ONLY route using POST and an
EXECUTION/DESTRUCTIVE POST route using PLAN_WRITE. These isolate the selected
method and destructive-scope contradictions. The actual constructor requires
READ_ONLY routes to use GET, READS and READ scope; COMMAND routes must avoid
GET, READ scope and READS role; DESTRUCTIVE routes must avoid GET and use
EXECUTION_RUN or ADMIN scope. The test does not enumerate that full matrix or
show that a caller possesses the declared scope.

The sixth test rejects HttpErrorContract(statuses=(200,)) and an error descriptor
with an extra headers key. The owner also requires integer, sorted, unique 4xx/
5xx statuses and a typed schema, but these two cases do not cover every type,
ordering, duplicate, boundary or schema failure. No actual HTTP status is emitted.

All rejection cases assert InvalidHttpApiContract only. They do not assert error
message, repr, length, redaction or cause/context behavior. The selected route
decoder wraps ValueErrors with their text and cause; this is not evidence of a
cause-free or universally bounded public error boundary.

## Benign metadata and ownership limits

The final test renders the canonical read descriptor and excludes fastapi,
uvicorn, dockerfile and mcp-streamable-http from lowercase repr. There is no
shared secret helper, injected credential, import-graph audit or runtime response
in this test. Four absent words in benign metadata do not prove general isolation
from process state or exclusion of arbitrary private material.

The structure under test is route data transformed into sorted descriptors and
reconstructed values. It protects a selected public vocabulary and local
consistency laws. Adapter implementation, request matching, authentication,
payload-limit enforcement, actual error redaction and provider effects require
their own evidence; the reviewed parity context does not add such evidence here.

Authoring read all 250 lines and seven tests plus selected actual HTTP schema,
error, route and aggregate constructors/decoders, read-route factory/helper,
lookup and private validators. This does not claim a full read of the 919-line
HTTP owner or its command-route inventory. Only this test gains coverage. No
source changes, application imports, executable tests, builds, providers or live
actions occurred. Documentation links, whitespace and the frozen source/test
guard were checked. Only this test companion gains coverage.
