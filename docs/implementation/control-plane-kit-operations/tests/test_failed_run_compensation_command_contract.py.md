Source: [control-plane-kit-operations/tests/test_failed_run_compensation_command_contract.py](../../../../control-plane-kit-operations/tests/test_failed_run_compensation_command_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 184-line suite contains three unittest methods for a compensation command's
descriptor/fingerprint, selected rejection cases and minimal module/source
availability. It constructs actual language values and reads loaded source text.
It does not inherit a PostgreSQL fixture, create a service, execute admission,
construct a compensation result or inspect persisted state. The main guard invokes
unittest on direct execution; this documentation task did not run the tests or
import the application.

target_module calls importlib.import_module on each invocation and returns None
only for ModuleNotFoundError naming the exact target. Missing transitive dependencies
and other errors propagate. Python import caching still applies; this is not a
forced reload. require_contract asserts module presence and returns it. The suite
does not directly test the loader's success/missing/nested-failure branches.

The local command helper supplies fixed workspace/request/run/plan and graph IDs,
desired revision seven, synthetic a-times-64 execution fingerprint, operator-a with
COMPENSATE scope, a synthetic authority-reference canary, POST_EFFECT_FAILURE and
idempotency key compensate-a. Source failure is a typed terminal failure with fixed
code/message and {"phase": "start"} details. Defaults are constructed before
keyword overrides; related coordinates are not automatically recomputed.

Although command receives a module argument, it calls require_contract again to
obtain the default reason enum. That extra module-presence lookup occurs even if
reason is overridden. The helper uses actual RunId, RecoveryAuthority, FailureEvidence,
BoundedEvidence and IdempotencyKey constructors; it does not bypass their admission
or provide independent implementations of their laws.

The first test compares the entire descriptor to a literal dictionary, covering
the command tag, lineage, revision, execution fingerprint, actor, reason, nested
failure and idempotency key. Thus extra or missing descriptor fields fail this
example's equality assertion. It also checks that intent_fingerprint matches a
lowercase SHA-256-shaped regular expression, but does not compare a known digest
or compute an independent expected fingerprint.

A second command changes only the private authority-reference value. Its public
descriptor must remain equal while its intent fingerprint must differ. The
original authority-reference canary must be absent from str(command), repr(command)
and the string rendering of its descriptor; the reissued reference must be absent
from the changed descriptor. The changed command's own str/repr are not separately
checked. These assertions concern the two supplied references, not arbitrary
secret values, logs, exceptions or transport output.

The actual
[compensation command owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py)
omits authority-reference material from descriptor and combines that public
descriptor with the SHA-256 of the UTF-8 authority reference when fingerprinting
intent. Its outer digest uses sorted compact JSON with ASCII escaping and SHA-256.
The actual
[RecoveryAuthority](../src/control_plane_kit_operations/execution_lease_recovery.py.md)
hides its reference field from generated repr, validates its text and normalizes
scopes. This explains the tested shape; neither construction nor hashing proves
authentication, a current lease or authorization by an external identity service.

The first test has three negative cases: OPERATE without COMPENSATE scope, a
malformed execution fingerprint and revision minus one. All must raise
InvalidOperationCommand. The actual scope-denied error also inherits that command
error, so the assertion accepts it without checking its exact subclass or message.
There are no positive boundary tests for revision zero, scalar-subclass tests for
revision/fingerprint, or exhaustive scope combinations in this method.

The result-named test checks hasattr for eight expected command/service/error/
record/result names. hasattr does not establish non-None values, callability,
exact type, root-export identity, field layout or a closed export list. It then
imports the target and calls its loader's get_source to find the literal strings
ActivityRunStatus.COMPENSATING, ActivityEventKind.RUN_COMPENSATION_STARTED and
RecoveryDecisionKind.BEGIN_COMPENSATION. This requires source availability from
that loader and is textual membership, not AST/control-flow validation.

Despite its name, that test does not bind a program/action/event to a constructed
result or execute a compensating state transition. A substring can occur outside
the relevant executable path. The actual owner contains those admission operations,
but their implementation does not turn these three source assertions into
transactional or behavioral result evidence.

The final test defines an empty RunId subclass and expects rejection when it is
supplied to the command. It also constructs FailureEvidence containing a
provider_message detail with credential-canary as its value and expects rejection.
The factories run inside assertRaises accepting either InvalidOperationCommand
or ValueError, so the assertion does not independently identify the exact rejection
stage/category or require fixed, unchained, bounded error rendering. The canary
is not checked against the raised error's text.

The inspected actual
[record/evidence constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
bound evidence structure/text and reject secret-shaped keys; they do not reject
arbitrary secret-shaped string values. provider_message is outside the compensation
command's smaller allowed key set: activity_id, node_id, phase and runtime_id,
whose values must be strings. The actual command also requires exact RunId,
so the two tests target a nested nominal boundary and that closed detail vocabulary.
They do not test a command subclass, every nested subclass, non-string detail
values or universal credential detection.

The source owner is a frozen command dataclass and has additional identifier,
authority, reason and idempotency checks. This suite does not assert all those
properties, test assignment immutability or establish durable idempotency/replay.
Its descriptor and private-reference fingerprint observations complement the
separate service/database tests; those tests receive no coverage credit here.

Read depth: the complete 184-line suite and all local helpers were read, with
retained full 529-line compensation owner context and refreshed command/descriptor/
fingerprint/source-failure validators. Selected actual RecoveryAuthority, RunId,
FailureEvidence/BoundedEvidence and IdempotencyKey contracts were checked. Full
records/workflows/recovery-authority modules and service suites were not reviewed
for this slice. Validation was documentation-only: local links, whitespace and
frozen-source comparison. No application imports, tests, database/provider calls,
credential access, source/inventory edits or publication were performed.
