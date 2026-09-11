Source: [control-plane-kit-core/tests/test_command_workflow_contract.py](../../../../control-plane-kit-core/tests/test_command_workflow_contract.py).
Maintain this document alongside its source file. Update it when the canonical
command catalogue, policy assertions, descriptor checks or rejection cases change.

This 476-line suite contains four tests of operator command contract values. It
imports the Core operations facade and a local descriptor assertion helper.
There is no workflow service, session store, authorization actor, execution
adapter or runtime fixture. The suite describes command meaning and selected
admission laws without executing those commands.

## An explicit ordered catalogue

The first test compares eight fields from every canonical command with an
explicit ordered list of 31 rows: operation ID, kind, family, stage, service role,
idempotency, approval and activity-history policy. It therefore pins membership,
order and those fields, rather than just comparing sets or deriving expectations
from the current command enum. Request/response schema names, payload policies
and the three session flags are outside this particular projection.

All rows require idempotency and history for accepted and rejected commands.
Most use PLAN/planning. Operation-session commands use PLAN/lifecycle;
approval.request uses PLAN/approval; approval.decide uses APPROVE/approval;
gateway-probe.request uses EXECUTE/observation. Activity-plan request, approval
request and desired-graph set submit for approval; approval.decide decides it;
desired-realized-projection.publish requires current approval. Other listed rows
use NOT_REQUIRED. That enum value is a contract label, not permission to register
keys, revoke authority or change live resources without the enforcing service's
authorization rules.

The actual [command owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py)
builds contracts from definitions, fixes required idempotency and accepted/rejected
history, then sorts by operation ID. Its workflow constructor rejects duplicate
IDs and duplicate kinds and requires the canonical operation-ID set. The test's
literal output table also checks the actual kind/family/stage/role/policy
association for each factory row; an aggregate set check alone would not do that.

## Descriptor reconstruction and disclosure checks

The second test fixes the descriptor's kind string, round-trips the canonical
mapping, excludes four words from its lowercase repr (postgres, unit_of_work,
fastapi and token), invokes the
[shared assertion helper](../../../../control-plane-kit-core/tests/contract_security_assertions.py)
and rejects an extra top-level key. The helper recursively visits Mapping values
and non-string/non-bytes Sequences, normalizes mapping keys to lowercase with
hyphens replaced by underscores, and rejects a fixed set of raw-secret field
names. It also excludes five known value markers from the lowercase repr.

These assertions inspect a benign factory-produced descriptor. They neither
inject secret-bearing operator input nor prove general redaction, authentication
or the absence of arbitrary sensitive values. Despite the test's “bounded” name,
it does not assert a byte, length, count or depth bound. The round trip is computed
mapping equality, not an independent canonical byte/hash vector. Only one extra
top-level field is a negative case here; missing keys, wrong types and nested
schema variations are not exhaustively tested. Error text, repr and cause/context
chains are not asserted. The selected owner decoder wraps ValueErrors with their
text and cause, so cause-free diagnostic guarantees must not be inferred.

## Selected session and payload laws

The third test looks up named commands and checks these declared properties:

- Session start uses REDACT_OPERATOR_VALUES and creates a session; close and
  cancel mark terminal session transitions.
- Desired-graph set requires an open session and uses GRAPH_DESCRIPTOR_REFERENCE.
- Realized projection publication, activity planning and approval request use
  their respective realized-graph, plan-reference and approval-risk policies.
- Runtime-authority register/revoke, delivery register/revoke and ingress-authority
  register/revoke use their respective authority-reference payload policies.

These are selected positive flags and policy enums, not a complete matrix of
every payload/session field. For example, the test does not directly assert that
session start's requires-open flag is false or test malformed terminal-session
flag combinations. The owner has additional constructor checks for those
contradictions. No session is opened/closed, graph replaced, payload redacted,
approval granted or authority revoked by these assertions.

## Three negative constructions

The last test constructs a workflow from two copies of activity-plan.request,
constructs that command with BEST_EFFORT idempotency, and constructs desired-graph
set with APPROVAL family. Each must raise InvalidCommandWorkflowContract.
The duplicate workflow candidate also lacks the rest of the canonical catalogue
and duplicates its kind; the actual source checks duplicate operation IDs first.
This is not an independent missing-member or duplicate-kind test. The other two
cases exercise required-idempotency and kind/family consistency guards. No
exception message or rejection precedence is asserted by this test.

## Evidence boundary and maintenance

The structure is command data transformed into a sorted catalogue and descriptor,
with policy values handed to later services. It does not prove durable
idempotency, actual accepted/rejected history, transactional session transitions,
secret custody or end-to-end command execution. Tests should continue to own this
Core value boundary rather than reconstruct Operations behavior here.

Authoring read the full 476-line suite and the full local assertion helper, plus
selected actual owner kind/family mapping, constructors, descriptors, lookup,
factory, definition rows and validation helpers. This does not claim a full read
of the 850-line commands owner, and only this test row gains coverage. No source
changes, application imports, executable tests, builds, provider calls or live
actions were performed. Validation is documentation links, whitespace and the
frozen source/test guard; independent review is pending.
