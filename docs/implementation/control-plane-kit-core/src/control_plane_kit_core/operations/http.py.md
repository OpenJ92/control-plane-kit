Source: [control-plane-kit-core/src/control_plane_kit_core/operations/http.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py).
Maintain this document alongside its source file. Recheck route/schema/error
admission, both route inventories, safety/scope relations and consumer assumptions
when changing the HTTP language.

This 919-line module owns pure HTTP API values: HttpSchemaRef, HttpErrorContract,
HttpApiRouteContract and HttpApiContract, all frozen dataclasses. Closed enums
name five methods (GET/POST/PUT/PATCH/DELETE), five auth scopes and three safety
classes. It imports the Core service-role enum, not a web framework. It does not
host a server, match requests, resolve schema classes, authenticate actors or
read/write provider state.

## Schema and error declarations

HttpSchemaRef has a name and max_bytes, defaulting to 65536. Names must be nonempty
ASCII letters, digits, dots, dashes or underscores. The limit must be an exact
integer from 1 through 1048576 inclusive; booleans are rejected. The name itself
has no length cap. This validates a declared limit, not an actual request or
response body, JSON object or aggregate descriptor size.

HttpErrorContract defaults to statuses (400, 401, 403, 404, 409, 422, 503) and
HttpSchemaRef("BoundedError", max_bytes=8192). Statuses must be a tuple of exact
integers, already sorted and unique, all between 400 and 599. An empty tuple is
not explicitly forbidden. The schema must be typed, but its name and bound need
not equal the defaults. Calling a schema BoundedError does not redact a payload
or constrain the size of exceptions raised by this module.

Both records emit exact small mapping shapes and reconstruct through their
validators: schema has name/max_bytes; errors has a status list and nested schema.
They do not read serialized HTTP messages or choose a status in response to an
operational failure.

## Route shape and safety laws

HttpApiRouteContract stores route ID, method, path template, service role, auth
scope, safety, request/response schema references and an error contract. Defaults
are EmptyRequest with 1024 bytes, JsonResponse with 65536 bytes and the shared
default error contract. All enum/record fields must have the expected types.
Route IDs use the same identity grammar as schema names.

Path validation requires a string starting with slash, different from the lone
root slash, without question mark, fragment marker or whitespace. It does not
parse placeholders, enforce balanced braces, normalize dot segments or repeated
slashes, resolve percent encoding or establish equivalence between templates.
There is no path-length cap or request matcher.

| Safety | Constructor requirements |
| --- | --- |
| READ_ONLY | GET, READS service and READ auth scope |
| COMMAND | Non-GET, non-READ scope and non-READS service |
| DESTRUCTIVE | Non-GET and EXECUTION_RUN or ADMIN scope |

The destructive branch does not separately exclude READS service role. Apart
from the table, route IDs, paths, schema names and service/scope combinations
are not bound to a canonical operation definition. These are source-derived
admission limits, not executed defects or evidence of an unsafe deployed route.
An auth-scope value is not a credential, and a safety label does not grant or
check current approval, idempotency or permission to perform an external action.

## Aggregate and mapping behavior

HttpApiContract requires a tuple of route values. It rejects duplicate route IDs
first, then duplicate exact (method, path_template) pairs. It permits empty or
partial catalogues; it does not require the built-in route inventories. Routes
sort by path template, method value and route ID. This is descriptor ordering,
not a router's request-match precedence. Different placeholder spellings are not
compared for equivalent URL patterns.

route(route_id) scans for the exact stored ID and raises InvalidHttpApiContract
with the unknown value if absent. It does not apply the route-ID grammar to the
lookup argument or execute the selected route. The aggregate descriptor contains
kind http-api-contract and the sorted route list. Route descriptors include all
nine named fields and nested request/response/error shapes.

Decoders require exact keys, expected outer kind, typed lists/mappings/text and
the constructor laws. Route decoding wraps selected ValueErrors with their text
and cause; outer aggregate/schema/error checks may raise directly. Arbitrary
Mapping callback failures are not universally translated. No raw JSON decoder,
canonical-byte/hash API, total descriptor byte/item/depth limit or general public
error redaction is provided. The declared response/error bounds do not constrain
these Python diagnostics.

## Full built-in read inventory

operator_read_http_routes returns 37 route values in its literal table order;
constructing HttpApiContract subsequently sorts them. The first factory row is
read.workspace. Every row uses GET, READS, READ scope and READ_ONLY safety via
_read_route. They retain EmptyRequest/1024 and the default error contract.

The complete table covers workspace, current/desired/operator graphs, overview,
control surface, activity and observed state; drafts, revisions, preparations and
attempts; sessions/actions/plans/approvals; plan detail/runs and run events;
approval detail/pending approvals; delegation keys, verifier configuration and
gateway probes; ingress/runtime authorities and runtime deliveries; secret
providers and secret references. Paths and schema names are explicit data.

Read responses declare 65536 bytes except revision preparations and revision
attempts, which declare 1048576. These two larger declarations do not prove a
live endpoint accepts, produces or rejects payloads at those sizes. Metadata
route names do not establish that actual secret/authority responses are redacted.

## Full built-in command inventory

operator_command_http_routes returns 37 explicit definitions through
_command_route. Every command uses POST, named request/response schemas with
65536-byte declarations and the default error contract. Revocation/deletion
actions are POST paths ending in revoke/delete where specified; the enum's
DELETE method is not selected by this factory.

| Command group | Service | Auth scope |
| --- | --- | --- |
| Workspace/product/image-pull, runtime/delivery/ingress authority, secret provider/reference and delegation-key administration | PLANNING | ADMIN |
| Session start/close/cancel/record-action | LIFECYCLE | PLAN_WRITE |
| Draft create/revise/select/delete, desired graph set, deployment plan/prepare | PLANNING | PLAN_WRITE |
| Approval request | APPROVAL | PLAN_WRITE |
| Approval decision | APPROVAL | APPROVAL_DECIDE |
| Gateway probe request | OBSERVATION | EXECUTION_RUN |
| Deployment admission | ADMISSION | EXECUTION_RUN |
| Run claim and graph advancement | LIFECYCLE | EXECUTION_RUN |
| Run start and deployment execute | EXECUTION | EXECUTION_RUN |
| Recovery decision | RECOVERY | EXECUTION_RUN |

Only command.deployment.execute is DESTRUCTIVE in this table; the other 36 are
COMMAND. That classification does not mean revocation, deletion or recovery is
consequence-free or automatically authorized. Required approvals and effect
semantics belong to the appropriate higher-level contract and enforcing service.
All rows, paths and schema names were read; no command was invoked.

## Actual consumers and test evidence

The fully reviewed [parity owner](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py)
resolves these route values to check role, schema-name, safety and auth-scope
agreement with projection/command/security bindings. It compares metadata rather
than measuring payloads. Selected actual
[process code](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/process.py)
stores an optional HTTP contract, requires its type when HTTP_API readiness is
declared and includes its descriptor. The selected
[server handoff](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py)
requires the process HTTP value to match both parity inputs. These are pure
composition consumers, not running server evidence.

A scoped symbol search in frozen Core and Operations source found HTTP record/
factory references in the owner, Core process/parity and facades, with no matching
Operations dispatcher use. This does not audit every external interpreter,
server repository, alias or dynamic reference.

The full [250-line HTTP suite](../../../../../../control-plane-kit-core/tests/test_http_api_contract.py)
has seven tests: literal read-route order and roles/safety, one complete command
descriptor and declared bounds, reversed-input path ordering and mapping round
trip, selected identity/path/duplicate and safety/scope negatives, two error-
contract negatives and four absent words in benign metadata. It does not test
every constructor freedom, declared size boundary, command-route path or actual
HTTP response. Retained full read-adapter346, command-parity515 and authorization-
history310 suites add mapping/policy composition evidence, not live enforcement.

Authoring completed all 919 source lines, both full route inventories and private
helpers, retained the full HTTP/parity test context and checked actual selected
process and handoff consumers. Only this owner gains coverage; other consumers
were not fully reviewed here. No source repair, application imports, executable
tests, builds, provider/live action or merge occurred. Documentation links,
whitespace and frozen source/test guards were checked; runtime evidence remains
the responsibility of the owning implementations.
