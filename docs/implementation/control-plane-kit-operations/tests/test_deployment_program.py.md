Source: [control-plane-kit-operations/tests/test_deployment_program.py](../../../../control-plane-kit-operations/tests/test_deployment_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 472-line pure suite has seven tests for deployment-program reference and
command values. It checks export ownership, selected immutability/field shape,
bounded text and exact outer types, desired-lineage/revision coupling, readiness
canonicalization, redacted repr/descriptors and finite source/import constraints.
It imports interpreter exports for identity checks but never constructs or calls
the interpreter. There is no database setup, runtime effect or durable program
preparation/progression in this file. The direct guard calls unittest.main.

_contract imports deployment_program and translates any ModuleNotFoundError into
a chained AssertionError naming #1629, without distinguishing a missing target
module from a nested missing dependency. It then imports the Operations root and
checks six expected attributes with hasattr. This is not a skip/optional fallback.
PACKAGE_ROOT and SOURCE_ROOT locate package files for later source inspection.

_context constructs an AuthenticatedPrincipal for issuer.example/operator-a with
an OPERATOR identity and a workspace grant containing PLAN_REQUEST and PLAN_EXECUTE,
then calls command_context for that workspace. The actual
[identity contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py)
derive context scopes from the matching grant and validate their agreement. These
are synthetic credential-free identity values created in the test, not proof that
an authentication service verified the named principal or current access policy.
No CredentialVerifier is invoked.

_lineage creates GraphProjectionLineage from graph-/projection-prefixed labels.
_prepare eagerly constructs defaults for context, empty desired DeploymentGraph,
current lineage, absent desired lineage/revision zero, title, prepare key and no
approval comment, then applies keyword changes. _progress similarly defaults to
context, workspace-a/plan-a reference, empty readiness and a progress key before
overrides. Both directly construct command values; neither checks durable graph
state or creates an operation session.

The export test requires module.__all__ to equal the ordered six-name expected
tuple, root attributes to be the same objects, and each name to appear in root
__all__. It compares DeploymentProgramCommand with Prepare | Progress and requires
DeploymentProgramStateConflict to be absent from the command module. Four
interpreter exports must separately match root identity and appear in root __all__.
These checks establish selected ownership and union shape, not runtime behavior
of those interpreter objects or completeness of all root exports.

Three constructed values—reference, prepare and progress—must have no __dict__ and
must raise FrozenInstanceError when assigning an extra attribute. The test does
not directly attempt to change every declared field or mutate nested graph/context
data. SavedDesiredTopologyRevision is present in expected exports but is not among
these constructed values; this file does not assert its frozen/slots behavior,
constructor bounds or saved-revision preparation semantics.

Reference testing accepts 512-character workspace and plan IDs and checks their
exact two-field descriptor. Six negative candidates cover empty workspace, each
oversized ID, newline-containing workspace, bool workspace and a str subclass.
_HostileText overrides __len__ to raise; receiving the expected contract error
demonstrates this selected input is rejected before that hook is used. It does
not instrument every possible special method or nested forged object.

assert_contract_error requires InvalidDeploymentProgramContract through
assertRaises, no cause/context, combined str/repr length at most 512 and absence
of each nonempty supplied canary. It accepts subclasses of that error rather than
asserting exact error type. Empty canaries are deliberately skipped. These are
explicit bounded/redacted error assertions for the supplied cases, not universal
behavior of all imported constructors or interpreter failures.

Prepare accepts absent expected_desired with revision zero and present lineage
with revision one. Ten invalid variants cover wrong context/desired/current/
desired-lineage outer values, absent desired with positive revision, present desired
with zero/negative/bool revision, wrong idempotency-key type and an oversized context
workspace. The last context can be constructed by the underlying identity language,
then is rejected by this command's tighter bound. Other exact-type checks are
exercised mostly with object(), not a complete subclass matrix for every field.

The actual
[command owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program.py)
requires exact context, lineage, key and permitted desired outer types; it does
not recursively validate a candidate DeploymentGraph or query durable truth.
It couples absence/presence of expected desired lineage with zero/positive revision.
Its current desired union also accepts SavedDesiredTopologyRevision, with separate
bounded selected-lineage requirements. No case in this suite constructs that
alternative, so the export assertion is not coverage of that branch.

The prepare representation test constructs title, approval-comment and graph-name
canaries. Its exact descriptor contains command kind, workspace, current lineage,
absent expected desired, revision, key and approval_comment_present=True. Combined
repr/descriptor must omit those canaries, operator/issuer and the PLAN_EXECUTE scope
string. It also checks dataclass repr=False for context, desired, title and comment.
This verifies omission from these representations; it does not remove those values
from accessible command fields or establish arbitrary nested redaction.

Accepted content boundaries are title length 512, comment None and comment length
512. Seven rejection cases cover empty/oversized/newline title, empty/oversized/
newline comment and bool comment. The helper applies bounded, unchained canary
checks. The test does not separately assert false approval_comment_present for
None, whitespace-only strings, all control codes or subclasses for these content
fields, although the owner has its own content predicate.

Progress creates two ExternalReadinessAttestation values in reverse order and
requires stored readiness sorted by activity ID. A 200-character activity ID is
accepted in a command; a 513-character ID must be rejected directly by the imported
attestation constructor with InvalidOperationCommand. That latter assertion has
no local contract-error redaction checks and does not pinpoint the first invalid
length above the 200-character boundary.

Eight progress negatives cover wrong context/reference, list readiness instead
of tuple, object or attestation subclass items, a duplicated identical attestation,
wrong key and context/reference workspace mismatch. The common helper checks
bounded unchained contract errors and selected canaries. The duplicate case uses
the same value twice, not two different evidence references for the same activity;
the actual owner rejects duplicate activity IDs before sorting. There is no test
of arbitrary readiness count limits, plan membership or resolution of references.

The actual
[readiness contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py)
validates canonical activity identity and an evidence_ref shaped as a bounded
namespaced identifier, not a URL/value. Progress requires exact attestation values
and retains a canonical tuple, but neither this command nor the test contacts an
external readiness source. _ReadinessSubclass is otherwise empty; rejection is
nominality evidence, not a probe of malicious overridden behavior inside it.

The progress representation test asserts an exact descriptor containing command,
workspace/plan reference, readiness_count=1 and key. Its combined repr/descriptor
must omit activity/evidence, operator/issuer and scope canaries. Context and
readiness dataclass fields must have repr=False. Reference and key remain public
coordinates in these selected representations; the tests do not claim all
caller-supplied text is globally secret-safe.

The final test checks exact ordered dataclass fields for reference (two), prepare
(eight) and progress (four), plus identity reuse of ExternalReadinessAttestation
and IdempotencyKey. The selected
[key owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
supplies its own nonblank/max-200 text constructor; the command checks exact key
type rather than reimplementing that contract. GraphProjectionLineage likewise
comes from the shared records owner, not a new program-local graph identity type.

For source structure, the test parses deployment_program.py and builds an exact
mapping from ImportFrom module strings to imported-name sets. It expects eight
module entries including future/dataclasses/typing and the five domain owners.
This mapping ignores ast.Import nodes, relative import level and duplicate names/
occurrences collapsed by sets. It does not constitute an exhaustive import DAG.
The test separately gathers ast.Name and ast.Attribute call spellings and forbids
open, exec, eval, compile, connect, commit, rollback and execute. That finite spelling
check does not resolve aliases, dynamic calls or transitive effects.

It also parses admission.py and workflows.py and collects their ImportFrom module
strings and Import alias names, rejecting any containing deployment_program. This
guards those two textual import directions; it does not traverse their dependency
closure or execute their source. Direct imports elsewhere and dynamic dependency
paths are outside that check. No tests are run during this documentation review.

The selected actual
[interpreter entry](../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py)
owns DeploymentProgram and its interpreter errors, accepts composed command services
and begins preparation with authorization/validation. These exported definitions
explain the test's module separation, but this slice does not review or test the
full interpreter, projections, persistence, child idempotency, replay or effects.
Constructing a command with an empty desired graph is not preparing a durable
program, progressing execution or approving resource deletion.

Read depth: the complete 472-line suite, all seven tests and all local helpers
were read with the complete 226-line command owner. The full Core identity owner,
selected readiness/key/lineage constructors and validators, root exports, actual
interpreter definitions/initial preparation boundary and textual admission/workflow
imports were checked. No full interpreter/admission/workflows/records or transitive
dependency review is claimed. Validation was documentation-only: local links,
whitespace and frozen-source comparison. No application imports, tests, database/
provider calls, credential access, source/inventory edits or publication were
performed.
