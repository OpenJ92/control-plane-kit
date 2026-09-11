Source: [control-plane-kit-core/src/control_plane_kit_core/operations/commands.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py).
Maintain this document alongside its source file. Recheck command vocabulary,
kind/family relations, canonical policies, descriptor admission and actual service
consumers when changing this contract.

This 850-line module describes the pure operator command workflow. Eleven command
families organize 31 command kinds; fourteen payload-policy values describe how
command material is intended to be represented. Two public frozen records hold
one command contract and a complete workflow catalogue. A private frozen
definition record and literal definition tuple feed the canonical factory.
There is no dispatcher, route, authorization actor, session store or callback.

## Public shape and constructor laws

OperatorCommandContract contains operation ID, kind, family, stage, service role,
request/response schema names, idempotency/approval/history/payload policies and
three session booleans. Schema names are strings naming a handoff, not imports or
instantiated request classes. Stage/role values come from the
[service language](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py),
while idempotency, approval and history policies come from
[parity contracts](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py).

Construction enforces these local laws:

- Operation ID and schema names are nonempty strings containing only ASCII
  letters, digits, dots, dashes and underscores. They have no length bound.
- Kind and family must be typed enum values and match the explicit kind/family
  mapping. Stage, service role and all policies must have their proper enum type.
- Idempotency must be REQUIRED; history must record accepted and rejected
  commands. BEST_EFFORT and NOT_RECORDED cannot pass these guards.
- Session flags are exact booleans. Creation cannot require an already-open
  session, while a terminal transition must require one.

The constructor does not fix operation ID, stage, service role, schema strings,
approval or payload policy to the canonical definition for a kind. It also does
not require a particular kind to create, require or terminate a session, beyond
the boolean consistency checks. Thus the canonical factory expresses a narrower
intended contract than arbitrary directly constructed records. Required
idempotency is a declaration here; no duplicate command is looked up or replayed.

## Canonical command catalogue

The factory builds one contract from each definition, forcing REQUIRED
idempotency and RECORD_ACCEPTED_AND_REJECTED_COMMANDS history for every row.
Most commands use PLAN/planning and approval NOT_REQUIRED. The following groups
summarize all 31 definitions; policy names describe data treatment rather than
performing it.

| Command group | Payload policy | Requires open session |
| --- | --- | --- |
| workspace.create | REDACT_OPERATOR_VALUES | false |
| product-descriptor.import | PRODUCT_DESCRIPTOR_DOCUMENT | false |
| image-pull-authority.register | IMAGE_PULL_AUTHORITY_REFERENCE | false |
| runtime-authority register/revoke | RUNTIME_AUTHORITY_REFERENCE | false |
| runtime-authority-delivery register/revoke | RUNTIME_AUTHORITY_DELIVERY_REFERENCE | false |
| ingress-authority register/revoke | INGRESS_AUTHORITY_REFERENCE | false |
| secret-provider register/revoke | SECRET_PROVIDER_REFERENCE | false |
| secret-reference register/revoke | SECRET_REFERENCE | false |
| delegation-key register/activate/retire/revoke | DELEGATION_KEY_REFERENCE | false |
| gateway-probe.request | GATEWAY_PROBE_REFERENCE | false |
| operation-session.start | REDACT_OPERATOR_VALUES | false |
| operation-session close/cancel/record-action | REDACT_OPERATOR_VALUES | true |
| desired-topology-draft create/revise/select/delete | GRAPH_DESCRIPTOR_REFERENCE | true |
| desired-graph.set | GRAPH_DESCRIPTOR_REFERENCE | true |
| desired-realized-projection.publish | REALIZED_GRAPH_PROJECTION_REFERENCE | true |
| activity-plan.request | PLAN_DESCRIPTOR_REFERENCE | true |
| approval request/decide | APPROVAL_RISK_EVIDENCE | true |

Within grouped rows, concrete operation IDs join the group and action with a dot,
as in `runtime-authority.register`. Image-pull authority belongs to product
registration; runtime delivery belongs to runtime authority; secret references
belong to the secret-provider family; draft and realized-projection commands
belong to desired graph. These associations are explicit in the kind/family map.

Session start is the sole canonical creates-session command; close and cancel
are the only terminal transitions. Operation-session commands use PLAN/lifecycle.
Approval request uses PLAN/approval and decision uses APPROVE/approval.
Gateway probe uses EXECUTE/observation. Other rows retain PLAN/planning.

Activity-plan request, desired-graph set and approval request submit for approval;
approval decision decides it; realized-projection publication requires current
approval. Other rows retain NOT_REQUIRED. This does not authenticate a caller or
authorize live registration, revocation, exposure or deployment. Service policy
and current authority must still be established by the enforcing boundary.

The internal definitions also provide request/response schema strings. Several
commands intentionally share response names, such as registration/revocation
pairs and operation-session results. There is no schema registry lookup or
compatibility check against an implementation class in this module.

## Catalogue integrity and lookup

OperatorCommandWorkflowContract requires a tuple of command contracts, rejects
duplicate operation IDs and duplicate kinds, and requires its operation-ID set
to equal the IDs in the literal definition tuple. It then sorts commands by ID.
The current factory's 31 rows and 31 distinct enum kinds cover the vocabulary;
the aggregate checks canonical IDs rather than explicitly comparing its kind set
with the enum. Adding kinds therefore also requires reviewing definitions and
tests instead of assuming enum growth alone updates this contract.

The aggregate does not compare each record with its canonical definition or bind
an ID to a particular kind. Distinct typed kinds with compatible families can be
assigned noncanonical IDs while retaining the required ID set. Other constructor-
permitted policy differences also survive aggregate construction. These are
source-derived admission limits, not executed regressions or evidence that a
service accepts modified catalogues. The canonical factory supplies the intended
associations, and the governing test explicitly checks many of them.

command(operation_id) validates the ID's text grammar and returns its matching
record, or raises an error containing the unknown ID. Unique IDs make lookup
unambiguous within an accepted catalogue. No command executes during lookup.

## Mapping representation and diagnostic limits

Each command emits fourteen named fields, converting enum values to strings and
retaining booleans. The workflow emits a fixed
`operator-command-workflow-contract` kind plus its ordered command list. Decoding
requires exact keys, the fixed outer kind, a list of mapping entries, typed text
and exact booleans, then invokes the constructors and sorting rules. Decoding
does not require the incoming command-list order to already be sorted.

The nested command decoder wraps selected ValueErrors with
InvalidCommandWorkflowContract(str(error)) and retains their cause. Outer shape
checks and some other errors occur outside that wrapper. There is no raw JSON
decoder, canonical-byte/hash function, byte/item/depth limit or universal handling
of arbitrary Mapping callbacks. Some diagnostics contain operation IDs or enum
conversion text. Restricted identity characters, exact keys and policy labels
are not a general secret-redaction or bounded-public-error guarantee.

## Actual consumers and evidence boundary

Selected actual [Operations workflow requests](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
use OperatorCommandKind.START_OPERATION_SESSION in a request descriptor.
[OperationActionRecord](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
accepts typed operator-command or lifecycle kinds as its action type. These are
concrete vocabulary consumers. They do not demonstrate that service behavior is
generated from this catalogue. A scoped symbol search in the frozen Core and
Operations source trees found no additional non-facade consumers outside this
module of the factory, public contract record names or CommandPayloadPolicy;
that observation is not a whole-repository dynamic-use audit.

The full [476-line governing suite](../../../../../../control-plane-kit-core/tests/test_command_workflow_contract.py)
has four tests. Its explicit ordered 31-row table fixes eight projected fields,
then other tests cover mapping round-trip, benign disclosure checks, selected
session/payload flags and three negative constructions. The
[test companion](../../../tests/test_command_workflow_contract.py.md)
explains why those checks do not establish a descriptor size bound, exhaustive
constructor admission, actual redaction or durable command execution.

Authoring completed all 850 source lines, retained the full test/helper context
and read selected actual shared policy enums, service enums and Operations
workflow-request/action-record consumers. Only this owner gains coverage; those
consumer modules were not fully reviewed here. Security and operational duties
remain with services: authenticating intent, checking current authority, enforcing
idempotency/session rules, redacting payloads and recording outcomes. No source
repair, imports, executable tests, builds, live actions or merge occurred.
Documentation links, whitespace and frozen source/test guards were checked;
independent review is pending.
