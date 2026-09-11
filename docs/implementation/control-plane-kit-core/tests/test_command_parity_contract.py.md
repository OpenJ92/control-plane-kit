Source: [control-plane-kit-core/tests/test_command_parity_contract.py](../../../../control-plane-kit-core/tests/test_command_parity_contract.py).
Maintain this document alongside its source file. Recheck the literal adapter
mapping, transaction fixture, policy guards and assertion limits when changing
command parity.

This 515-line suite contains five tests and three local construction helpers.
It builds pure HTTP, MCP and unit-of-work contract values, then checks their
command bindings. No HTTP request, MCP invocation, database transaction, worker
or provider effect is executed by this test source.

## Fixture composition

_program creates one ApplicationServiceBinding per current service-role enum,
using a role-derived service name. _transaction_rule gives READS read-only store
participation and AUTHORIZATION none. EXECUTION gets read-write participation,
transaction ownership, AFTER_COMMIT effects, a worker and runtime authority;
all other roles get read-write participation and transaction ownership with the
remaining defaults. _uow combines those values for every current role.

The actual [transaction owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py)
validates and orders those boundaries. This is a fixture of declared service
requirements, not an opened unit of work or evidence that a worker/authority is
available. Tests build the HTTP catalogue from operator_command_http_routes and
use the default McpStreamableHttpContract value; neither starts a server.

## Explicit route and tool mapping

The first test compares six fields from every canonical binding with an explicit
ordered list of 37 rows: operation ID, HTTP route ID, MCP tool name, service role,
idempotency and approval. It therefore fixes these values and their order rather
than just comparing sets or following the current enum. For example,
product-descriptor.import maps to command.product.import and
import_product_descriptor; route IDs are not universally obtained by prefixing
the operation ID. The table includes planning/preparation, admission/execution,
claim/start, recovery and advancement as well as operator registration/session
commands. It is a separate adapter catalogue from the 31-row command-workflow
test, not a claim that their row sets are identical.

All 37 rows require idempotency. Approval request, deployment plan/prepare and
desired-graph set submit for approval; approval decision decides it. Deployment
admit/execute, graph advancement, recovery decision and run claim/start require
current approval. Remaining listed rows use NOT_REQUIRED. These declarations
do not bypass authentication or grant authority to mutate live resources.

The actual [parity owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py)
builds bindings from its definitions, fixes required idempotency, checks each
against the supplied route/transaction values and sorts by operation ID. The
test's six-field projection omits request/response schemas, concrete HTTP paths,
methods and auth scopes. Factory construction checks schema agreement with the
provided HTTP contract, but the test has no independent literal schema table.
MCP names are compared as metadata; no registered tool or response is inspected.

## Descriptor evidence

The second test asserts the outer adapter-command-parity-contract kind and
round-trips the computed descriptor, including its nested HTTP, MCP and
unit-of-work contracts. One extra outer key must raise InvalidAdapterParityContract.
This does not exhaust missing keys, wrong types or nested malformed values and
does not fix independent wire bytes or hashes. No size/depth/count bound,
secret canary, diagnostic text/repr or exception chain is asserted. Selected
owner decoders retain ValueError text and causes, so this suite is not evidence
of cause-free public errors or general redaction.

## Role and transaction-policy negatives

The third test supplies a single deployment.execute binding with PLANNING role
against an EXECUTION route and expects InvalidAdapterParityContract. It then
constructs a weaker execution boundary retaining read-write participation and
transaction ownership but leaving effects FORBIDDEN and worker/runtime-authority
flags false. A correctly role-bound execute command against that boundary must
raise the same exception.

Selected actual [HTTP route data](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py)
marks command.deployment.execute DESTRUCTIVE with ExecuteDeploymentRequest and
ExecutionRunResponse schemas. The parity guard checks role and schemas, rejects
read-only command routes, requires a read-write transaction owner and requires
AFTER_COMMIT for destructive routes. The weakened fixture is valid as a unit-of-
work value and is rejected at parity composition. It changes multiple flags;
the selected parity guard checks AFTER_COMMIT rather than independently requiring
worker/runtime-authority flags. The test does not separately prove either flag
mandatory, nor does it demonstrate commit-before-effect execution.

These are deliberately singleton command tuples. The parity constructor validates
supplied bindings and uniqueness; it does not demand the entire canonical command
catalogue. Missing the other 36 rows is not the rejection being tested.

## Destructive policy and duplicate identities

The fourth test makes two deployment.execute candidates: BEST_EFFORT idempotency
with current approval, then REQUIRED idempotency with approval NOT_REQUIRED.
Both must fail parity construction. The owner checks destructive-route
idempotency and approval before its additional approval-gated idempotency rule;
the first candidate also contradicts that later rule. Tests assert the error
class, not diagnostic precedence. They cover this one destructive command, not
every route classified as destructive or every approval/idempotency combination.

The fifth test repeats the same deployment.plan binding twice. Operation ID,
HTTP route ID and MCP tool name are all duplicated together. The owner checks
operation-ID uniqueness first, then route and tool uniqueness. This case is not
three independent collision tests, and it asserts no error message or precedence.

## Ownership and validation limits

The tested structure is a relation among command bindings, HTTP route contracts,
MCP transport metadata and service transaction declarations. It rejects selected
inconsistent compositions. Live transport parity, policy authorization, durable
idempotency/history, transaction order and external-effect results remain the
responsibility of their owning implementations and evidence.

Authoring read all 515 lines, all five tests and three helpers, plus selected
actual parity binding/aggregate/decoder/factory/definition sections, transaction
constructors/lookups and HTTP route construction/execution data. It does not
claim a full read of the 1,407-line parity owner or the HTTP/transaction owners.
Only this test row gains coverage. No source fix, imports, executable tests,
builds, live operations or merge occurred. Documentation links, whitespace and
the frozen source/test guard were checked; independent review is pending.
