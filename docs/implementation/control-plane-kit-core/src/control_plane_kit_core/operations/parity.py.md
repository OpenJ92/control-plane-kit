Source: [control-plane-kit-core/src/control_plane_kit_core/operations/parity.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py).
Maintain this document alongside its source file. Recheck binding vocabulary,
route/schema/policy agreement, both definition tables, completeness and disclosure
claims when changing adapter parity.

This 1,407-line module owns pure relations among adapter operation names, HTTP
contracts, MCP metadata and service transaction declarations. Three frozen
binding records and three frozen aggregate records describe projections,
commands and their security policies. Four enums name idempotency, approval,
activity-history and error-disclosure policies. None of these values hosts an
adapter, checks an actor credential, persists history or executes a command.

## The three compositions

```text
HTTP contract + MCP contract + projection bindings
  -> AdapterParityContract

HTTP contract + MCP contract + unit-of-work boundary + command bindings
  -> AdapterCommandParityContract

projection parity + command parity + security bindings
  -> AdapterOperationSecurityParityContract
```

The module imports [HTTP route contracts](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py),
[MCP transport metadata](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py),
the service-role vocabulary and
[unit-of-work declarations](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py).
Schema and tool names are strings establishing relations, not loaded classes or
registered callbacks. Factories provide the intended catalogues; constructors
validate the supplied values with the particular limits below.

## Binding values and local admission

AdapterProjectionBinding holds operation ID, service role, projection-schema
name, HTTP route ID and MCP tool name. AdapterCommandBinding replaces the single
projection schema with request/response schema names and adds idempotency and
approval policies. AdapterOperationSecurityBinding holds operation ID, role,
route ID, MCP name, auth scope, safety, history and error-disclosure policies.

Each binding checks typed enums and nonempty identity/schema/name text containing
only ASCII letters, digits, dots, dashes and underscores. There is no identity
length cap. These checks do not resolve route IDs or validate policy agreement;
the aggregates do that. Standalone command bindings permit either idempotency
enum value and any approval enum value. Standalone security bindings likewise
permit both history and error-disclosure policies, leaving composition to reject
inappropriate choices.

The four policy vocabularies are REQUIRED/BEST_EFFORT idempotency;
NOT_REQUIRED/SUBMITS_FOR_APPROVAL/DECIDES_APPROVAL/REQUIRES_CURRENT_APPROVAL;
NOT_RECORDED/RECORD_ACCEPTED_AND_REJECTED_COMMANDS history; and
BOUNDED_REDACTED/TRANSPORT_PRIVATE disclosure. They are policy data, not implementations.

## Projection parity

AdapterParityContract requires typed HTTP/MCP values and a tuple of projection
bindings. It separately rejects duplicate operation IDs, route IDs and tool
names, in that order. For each binding it resolves the actual supplied HTTP
route, checks role and response-schema-name equality, and requires READ_ONLY
route safety when the binding's role is READS. It then sorts by operation ID.

The constructor does not require every binding to use READS or demand the full
canonical projection inventory. Empty/subset tuples are not rejected merely
for incompleteness; unrelated HTTP routes may remain unbound. MCP tool names
are not checked against a live or declared tool registry. The canonical read
factory supplies the stricter intended READS-only catalogue.

operator_read_projection_parity translates the complete literal read table into
37 bindings. It fixes READS role and takes operation ID, route ID, tool name and
schema name from each row. The table covers workspace/current/desired/operator
graphs, drafts and saved revisions with preparations/attempts, sessions and
their actions/approvals/plans, plan/run histories, observations, control surface,
keys/verifier configuration/probes and authority/secret-reference metadata.

Names are related explicitly: read.activity-timeline uses route read.activity
and tool get_activity_timeline; read.open-sessions uses read.sessions and
list_open_sessions. Current and desired graph reads share GraphReadResponse.
No rule infers all names by string transformation. Every table row was read;
schema names describe expected projections without proving their runtime content.

## Command parity

AdapterCommandParityContract requires typed HTTP/MCP/unit-of-work values and a
tuple of command bindings. It rejects duplicate operation, route and tool names
within that tuple, then checks each binding against its supplied HTTP route and
service boundary:

- Role and both request/response schema names must agree with the route.
- A command cannot use a READ_ONLY route. Its service must participate read-write
  and own the operator-command transaction.
- A DESTRUCTIVE route requires REQUIRED idempotency, current approval and
  AFTER_COMMIT external-effect policy, checked in that order.
- Any current-approval binding additionally requires REQUIRED idempotency,
  whether or not its route is destructive.

Bindings sort by operation ID. The aggregate does not require the full canonical
command set, bind operation ID to a prescribed route/tool name, or independently
require worker/runtime-authority flags. Other typed idempotency/approval choices
can pass where the listed guards do not restrict them. The unit-of-work object
has its own admission laws, but neither object performs a transaction or checks
current approval, actor authority, retries or provider effects.

operator_command_parity translates its complete 37-row table and fixes REQUIRED
idempotency for every binding. Its table includes authority/secret/key/product
registration, workspace/session/draft editing, graph set, planning/preparation,
approval, admission/execution, claim/start, advancement and recovery commands.
It is not the separate 31-row operator command language's table: for example,
it names deployment.plan and deployment.prepare rather than activity-plan.request,
and supplies HTTP-facing schema names such as CreateWorkspaceRequest.

The table fixes current approval for deployment.admit/execute, run.claim/start,
graph.advance-current and recovery.decide. Approval request, deployment.plan,
deployment.prepare and desired-graph.set submit for approval; approval.decide
decides it. Remaining rows use NOT_REQUIRED. Most roles are PLANNING; session
commands, run.claim and advancement use LIFECYCLE; approval uses APPROVAL;
admission uses ADMISSION; start/execute use EXECUTION; recovery uses RECOVERY;
gateway probe uses OBSERVATION. NOT_REQUIRED does not authorize a caller to
register credentials, revoke authority or otherwise mutate external state.

## Authorization, history and error-policy composition

AdapterOperationSecurityParityContract requires typed projection/command parity
and equal MCP contract values. Its operations tuple must have unique operation
IDs and cover exactly the union of IDs in the supplied parity values. It does
not independently demand the two canonical 37-row catalogues. Thus completeness
is relative to its inputs, not all application routes or all possible operations.

For projection entries, it checks role, route ID and MCP name against the binding,
auth scope against the route and READ, safety READ_ONLY and history NOT_RECORDED.
For command entries, it checks role/route/name and route auth-scope agreement,
rejects READ auth, requires route-safety equality and accepted/rejected command
history. Every operation must declare BOUNDED_REDACTED errors. Entries sort by ID.
operation(operation_id) returns the matching record or raises an error with the
unknown value; it does not execute or authorize that operation.

Cross-family operation-ID disjointness is not separately required by the two
input parity constructors or this aggregate. Its validation selects a projection
first when an ID exists in both inputs. The security factory concatenates both
families, so duplicate IDs there are rejected by aggregate uniqueness instead.
There is no additional global route/tool-name uniqueness check across families.
These are source-derived composition limits, not executed defects or proof that
an application supplies colliding catalogues.

operator_adapter_security_parity derives auth scopes from each family's route
contract, fixes read safety/history and command history, takes command safety
from its route, and gives both families BOUNDED_REDACTED policy. The canonical
37-read/37-command composition yields 74 security entries. It does not require
the read and command HTTP contracts to be equal; the later server handoff adds
that composition constraint.

## Descriptors and error boundaries

Bindings serialize their fields directly, converting enums to strings. The
three aggregates emit distinct kind strings and nested HTTP/MCP/parity or
unit-of-work descriptors. Decoders require exact outer/binding keys, expected
kind strings, list sequence fields and mapping entries, then rebuild values
through constructors. Aggregate sorting normalizes record order; decoding does
not require incoming lists to already be sorted.

Selected ValueErrors, including nested contract errors, are wrapped with their
text and cause. Several shape/lookup checks occur outside wrappers. Helpers check
Mapping/text types, identity grammar and duplicate values; there is no raw JSON
parser, canonical-byte/hash API, aggregate byte/item/depth cap or universal
handling of custom Mapping callbacks. Unknown values may appear in diagnostics.
BOUNDED_REDACTED is an obligation represented by a field, not a universal property
of this module's exceptions or arbitrary descriptor contents.

## Actual consumers and evidence

Selected actual [server handoff code](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py)
stores all three parity values. It checks process HTTP/MCP agreement with read
and command parity, command unit-of-work agreement and security parity's exact
read/command inputs. This is a concrete pure composition consumer, not proof
that a server has started or its transports implement these declarations.
Commands and execution contracts also import this module's policy enums.

A scoped search in frozen Core and Operations source found these parity
record/factory symbols in the owner, Core facades and handoff; it did not show
an Operations dispatcher interpreting these catalogues. That search is not an
audit of every external repository, alias or dynamic use. Service implementation
and live parity require their own evidence.

The fully read governing suites comprise
[read-adapter tests](../../../../../../control-plane-kit-core/tests/test_adapter_parity_contract.py)
(346 lines), [command tests](../../../../../../control-plane-kit-core/tests/test_command_parity_contract.py)
(515 lines) and [authorization/history tests](../../../../../../control-plane-kit-core/tests/test_authorization_history_parity_contract.py)
(310 lines), each with five tests. They fix literal read/command mappings and
selected security policies, mapping round trips, benign disclosure checks and
selected contradictory compositions. Repeated-binding negatives overlap all
three identity collisions; weak execution fixtures change multiple flags;
metadata error-policy tests inspect no actual error response. These suites do
not prove live HTTP/MCP equivalence, actor authorization, durable history,
redaction, concurrency, idempotent effects or transaction ordering.

Authoring read all 1,407 lines, both full definition tables and every private
validator/descriptor helper, retaining all three full test suites and the shared
descriptor helper. Actual dependency context and selected handoff 1–189 plus
command/execution imports were checked. Only this owner gains coverage; the
handoff and other consumers were not fully reviewed here. No application source
fix, imports, executable tests, builds, providers, live operations or merge
occurred. Documentation links, whitespace and frozen source/test guards were
checked; this remains a separate owner packet for independent review.
