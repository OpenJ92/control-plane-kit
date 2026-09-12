Source: [control-plane-kit-operations/tests/test_deployment_program_projections.py](../../../../control-plane-kit-operations/tests/test_deployment_program_projections.py).
Maintain this document alongside its source file. When the test or its imported
projection/value contracts change, verify and update this companion in the same
change.

This 788-line suite contains eleven tests governing the 21 deployment-program
projection variants. It verifies public value shape, nominal identity, status
subsets, descriptors, selected error/redaction bounds and finite source/inventory
constraints. It constructs actual pure values and reads source/inventory files;
it does not load deployment history, execute commands or prove that any named
projection corresponds to a real running/completed deployment.

PROJECTION_NAMES is a fixed ordered tuple of all 21 variant names. EXPECTED_EXPORTS
prepends DeploymentProgramProjection. _Case carries the class name, constructor
argument dictionary, expected stage, expected descriptor tag and a tuple identifying
scalar identity fields to probe. _cases constructs one representative of every
variant, using workspace-a/plan-a, run-a, activity-a, attempt one and ordinary public
event/request/decision strings. Dictionary insertion order supplies the expected
dataclass field order.

The actual
[projection owner](../src/control_plane_kit_operations/deployment_program_projections.py.md)
defines frozen slots variants with a shared reference, class-level stage/tag and
variant-specific fields. It reuses the command owner's contract error and Core/
Operations identity/status types. It does not validate historical state or grant
authority. The test case matrix records the public representation, not a state
machine that chooses a variant from durable records.

_contract imports the module and verifies that the Operations root has every
expected export. A missing target module becomes an AssertionError chained from
the import failure; a nested ModuleNotFoundError is reraised. _run_id similarly
imports the Core run-identity owner and distinguishes its own missing module from
an absent nested dependency. _reference constructs the actual bounded reference;
_attempt constructs EffectAttemptIdentity from a nominal RunId, activity string
and attempt one. None of these helpers resolves those identities in a store.

_descriptor builds the expected outer dictionary from case tag, reference descriptor
and stage.value, then converts ActivityId to its value, EffectAttemptIdentity through
its descriptor, other objects with value attributes to those values, and leaves
remaining scalars unchanged. This is independent of the projection descriptor
method but deliberately shares the imported reference/attempt serializers. Equality
therefore protects the outer projection shape without independently proving every
nested serializer.

The vocabulary/inventory test checks the six DeploymentProgramStage members and
four ExecutionRequestStatus members in exact order. It checks the run-status enum
has ten members, plus small ActivityId, attempt and reference descriptor witnesses;
it does not enumerate all ten run-status names in that assertion. Inventory comes
from CPK_PACKAGE_MODULE_INVENTORY when set, otherwise the repository architecture
JSON. Filtering the legacy deployment-values destination must yield one row with
owner operation. This is a selected ownership assertion, not a whole-inventory
validation or authorization to change it.

The same test temporarily replaces importlib.import_module to inject a nested
projection dependency failure, calls _contract, checks the resulting error's name
and restores the original function in finally. It checks helper error propagation,
not production runtime import recovery; exception identity is not asserted there.

The export/union test requires module.__all__ to equal EXPECTED_EXPORTS in order,
root objects to be identical to module objects and each name to belong to root
__all__. The root's entire export set/order is not constrained. It requires a
UnionType and exact ordered names from get_args. For every case it constructs a
value, requires absence of __dict__ and requires extra-attribute assignment to
raise FrozenInstanceError. This does not independently test assignment to every
declared field, recursive freezing, class-attribute mutation or object.__setattr__
bypasses. The union does not seal subclassing in Python.

The field/stage/descriptor test constructs all cases and compares dataclass field
names to the complete ordered argument dictionary, checks stage by object identity,
requires stage absent from the constructor signature and compares the descriptor
to _descriptor(case). Class stage/tag are not data fields. These assertions do not
test descriptor round-trip parsing, a schema-version protocol, actual event
existence or semantic consistency with a plan/run record.

The reference/required-argument test tries an unrelated object and a subclass of
DeploymentProgramReference in every variant, requiring a contract error. It then
removes each argument in turn and requires TypeError. The actual source uses an
exact outer-type reference check; it relies on ordinary construction in the
[command owner](../src/control_plane_kit_operations/deployment_program.py.md)
for bounded workspace/plan strings. Forged exact-type references are not tested.
Missing-argument TypeErrors do not go through the bounded contract-error helper.

Scalar identity probing uses the case's identity_fields, explicitly skipping run_id.
Every listed event, approval-request/decision and execution-request ID accepts 512
characters. Six rejected candidates cover object, bool, empty string, 513 characters,
newline plus canary, and _HostileText. That str subclass raises if len is called;
the source's exact-str guard must reject it before reaching that hook. These cases
do not establish rejection of whitespace-only strings, DEL or all Unicode/UTF-8
edge cases. The actual 512-character helper permits whitespace and DEL and is
not a secret-content filter.

The run test derives the nine cases containing run_id, asserts that count, requires
an exact RunId retained with descriptor text run-a, and rejects a raw string,
RunId subclass and object in every case. The imported
[RunId owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py)
uses the canonical ASCII grammar, bounded at 200 characters. This suite verifies
projection nominality; it does not repeat all underlying run-grammar negative cases
or rebuild forged nominal objects.

Seven status matrices enumerate each current enum universe. Session-stopped accepts
CLOSED/CANCELLED; plan-stopped SUPERSEDED/CANCELLED; execution-stopped
CANCELLED/ABANDONED. Both effect-in-flight and recovery-required accept only
RUNNING/PAUSED/COMPENSATING. Execution-failed accepts only FAILED; execution-settled
accepts COMPENSATED/PARTIALLY_FAILED/UNCOMPENSATED_FAILURE/CANCELLED. Every other
member of each corresponding universe must fail. Each matrix also rejects a status
from another enum family, the accepted status's raw string value and an object.

This protects exact enum-family membership even where StrEnum text overlaps. It
does not prove that a persisted session/plan/run currently has the supplied status.
Run-only variants such as execution-running and advancement-ready have no status
field to validate. Stage assignment and status subset are representation laws,
not approval, scheduling or transition authority.

The two live-attempt variants accept congruent 200-character RunIds under every
accepted live status. They reject an object, EffectAttemptIdentity subclass and
an exact attempt naming another run. The source compares attempt.run_id and run_id
by value, not identity. The actual
[effect-attempt contract](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
normally validates exact RunId, canonical activity string and positive bounded
attempt integer. Those nested constructor laws are not exhaustively retested here.
Neither variant includes an effect-attempt status/outcome; the tests do not prove
an effect is in flight, uncertain or eligible for recovery.

Readiness-required accepts and preserves a 200-character ActivityId by identity,
emits its value in the descriptor, and rejects object, raw string and ActivityId
subclass. It checks neither actual readiness evidence nor membership in the
referenced plan. Ordinary
[ActivityId construction](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py)
owns the canonical activity string grammar; the projection only requires its exact
outer type.

assert_contract_error requires InvalidDeploymentProgramContract, no cause/context,
combined str/repr length at most 512 and absence of each nonempty supplied canary.
assertRaises accepts subclasses of the named error. The normal repr/descriptor test
constructs the representative cases, requires combined length at most 4096 and
absence of seven literal substrings: secret://, credential, private_key,
provider_endpoint, failure_body, graph_descriptor and readiness_evidence.

Those positive cases contain no sensitive payload to begin with. The 4096 bound
and substring checks apply to sample values, not every combination of maximum-length
public identities. The source does not forbid those substrings inside admitted
scalar IDs. A separate hostile event-ID case checks rejection without exposing
its candidate. The test also requires the imported contract-error object to be
identical to the shared error and the projection module to lack the interpreter's
DeploymentProgramStateConflict. These assertions do not promise universal nested
exception sanitization, trace/log redaction or encryption.

The final source guard parses the owner with AST and collects import modules into
a set. Actual imports must be a subset of ten allowed modules, including alternative
Core lifecycle/recovery/services paths; it does not require all allowed modules.
It rejects selected import-name substrings: postgres, store, schema, adapter,
cpk_server, socket and subprocess. Relative import levels are not preserved by
this collector, and order, duplicate imports and imported symbol identities are
not compared.

It separately rejects seven observed direct function names or called attribute
names: open, connect, commit, rollback, execute, transaction and callback. It parses
deployment_program and requires no collected import name containing
deployment_program_projections. These finite guards do not resolve aliases,
dynamic imports/calls or dependency implementations, and do not establish a whole
transitive acyclic/effect-free package graph.

The final test reads the configured/default inventory again, selects the same
single legacy destination and requires EXPECTED_EXPORTS to be included in its
canonical_public_exports, then repeats module/root object identity checks. The
actual selected row contains additional legacy vocabulary; inclusion is not proof
that those other names belong to this source. No inventory edit or publication
is performed by this documentation slice.

The tests establish a small representation boundary without production network,
authentication or durable mutation. Actual preparation consumes only three variants;
the full union and all case constructors do not imply that progression is implemented
for every variant. Provider behavior, history projection precedence, authorization,
transactional replay, recovery execution and cleanup require separate owning tests.

Read depth: full test788, every case/helper and full projection owner528 were read
in this continuous slice and retained for authoring. Actual command/reference,
RunId and private identity grammar owners, selected ActivityId/attempt validators,
enum declarations, root reexports and default inventory row were inspected. Full
command/interpreter and selected preparation adapter reading were retained. No
full Core recovery/planning, records, server, inventory or transitive dependency
review is claimed. Validation was documentation-only: links, whitespace and
frozen-source comparison. No application imports, executable tests, database,
provider, credentials, source/inventory changes or publication were performed.
