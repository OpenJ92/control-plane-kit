Source: [control-plane-kit-operations/src/control_plane_kit_operations/deployment_program.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 226-line module owns pure deployment-program references and preparation/
progression command values. It exports six names: the two-command union, program
reference, contract error, PrepareDeploymentProgram, SavedDesiredTopologyRevision
and ProgressDeploymentProgram. It does not define DeploymentProgram or its
interpreter errors, resolve saved catalogue input, inspect durable state, request
approval, execute readiness checks or run runtime effects.

InvalidDeploymentProgramContract derives from ValueError. Four public value
classes are frozen slots dataclasses. Their constructors validate ordinary input
shape and selected coupling laws; they do not recursively freeze contained graphs
or provide complete admission for arbitrary forged exact-type objects. No value
owns a transaction, callback, history writer or retry loop.

DeploymentProgramReference consists only of workspace_id and plan_id. Both use
_bounded_identity: exact str, nonempty, at most 512 characters and no code point
below 32. The exact-type check precedes len, so an ordinary str subclass cannot
inject a length hook through this helper. The helper does not use an ASCII ID regex,
strip whitespace, enforce UTF-8 encodability or independently resolve either ID.
Its descriptor returns precisely those two fields. A reference identifies claimed
program coordinates; it is not proof that a program exists or the caller may use it.

SavedDesiredTopologyRevision contains draft_id under the same identity bound and
revision as exact int from one through 2**63-1, rejecting bool. It has no workspace,
graph, selection flag or descriptor method of its own. Its name denotes an exact
catalogue coordinate, not authority to select that revision or evidence that it
is the currently selected desired graph. Database existence, ownership and catalogue
state are outside this constructor.

PrepareDeploymentProgram has eight fields: context, desired, expected_current,
expected_desired, expected_desired_graph_revision, title, key and optional approval
comment. Context must be exact TrustedCommandContext, and its workspace ID is
checked again with this module's tighter identity bound. The actual
[identity owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py)
derives context from an authenticated principal's matching workspace grant and
checks scope agreement. This module does not call a credential verifier, reconstruct
all nested principal/grant fields or apply a preparation policy-scope decision.

Desired must be exactly DeploymentGraph or SavedDesiredTopologyRevision. A graph
candidate is retained as supplied; the command does not compile or validate it.
expected_current must be exact GraphProjectionLineage. expected_desired is either
None or the same exact lineage type. The imported
[lineage record](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
normally validates its two text fields; the generic _lineage helper here checks
only its outer type.

The expected desired revision is exact nonnegative int. Absent expected_desired
requires zero, while present expected_desired requires a positive revision. A
DeploymentGraph candidate adds no local upper bound beyond that coupling. These
are optimistic expected-state coordinates, not a read of workspace state and
not automatic selection of the candidate as desired truth.

Saved-revision preparation has additional constraints. expected_desired must be
present, its expected workspace revision must not exceed 2**63-1, and both fields
of both current and desired lineages are rechecked through _bounded_identity.
Thus saved preparation strengthens the ordinary lineage outer-type check to exact
bounded strings. Its three revision concepts remain distinct: saved draft revision,
expected workspace desired revision and graph/projection identities. The constructor
does not require the two revision integers to equal each other or prove the selected
lineage came from that saved draft.

Title and any non-None approval comment use _bounded_content: exact str, nonblank
after strip, at most 512 characters and no control code below 32. None represents
no comment; empty text is not an equivalent admitted comment. The key must be exact
IdempotencyKey. Its imported
[workflow value](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
supplies nonblank text and a 200-character upper bound. This module checks the
outer key type rather than recomputing its internal validity or generating child
keys. The presence of a key is not evidence that a retry has been persisted.

Prepare hides context, desired, title and comment from its generated repr. Its
descriptor contains command kind, workspace, current/optional desired lineages,
expected desired revision, key and approval_comment_present. It omits principal,
scope, candidate graph, saved draft identity/revision, title and comment contents.
This selected descriptor is not a lossless command serialization or a full intent
fingerprint: different private content can have the same descriptor. Hidden fields
remain accessible in memory and are not erased or encrypted.

ProgressDeploymentProgram contains context, reference, readiness and key. Context
uses the same exact-type/workspace bound, reference must be exact
DeploymentProgramReference, and their workspace IDs must match. This equality
does not load the referenced plan or check that it belongs to the workspace. There
is no current graph, saved selection, approval decision, execution claim or runtime
status lookup inside this command value.

Readiness must be an exact tuple whose elements are exact
[ExternalReadinessAttestation values](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py).
Duplicate activity IDs reject the command, even if their evidence references differ.
The admitted tuple is sorted by activity_id and stored with object.__setattr__.
Empty readiness is permitted and this owner adds no tuple-count bound. It does
not reconstruct individual attestations, validate plan membership, resolve their
evidence references or infer actual readiness from their presence.

The imported attestation constructor requires canonical activity identity and a
nonempty namespaced evidence reference of at most 256 characters, shaped by its
reference regex rather than a URL or arbitrary value. Those are value-shape laws,
not a provider observation or authentication of external evidence. The Progress
constructor relies on ordinary attestation construction plus its exact outer-type,
workspace and uniqueness checks.

Progress hides context/readiness from repr. Its descriptor contains command kind,
reference descriptor, readiness_count and key, omitting activity/evidence contents
and authority material. Like Prepare's descriptor, it intentionally does not encode
all command intent. The two commands form DeploymentProgramCommand; the reference
and saved revision are supporting values, not additional command variants.

Helpers raise fixed contract messages naming fields rather than inserting rejected
values. Their local validation paths do not deliberately chain underlying errors.
The focused suite checks bounded messages and no cause/context for supplied cases,
but constructors call imported values/methods and use set/sort on retained fields;
the module is not a universal hostile-object or exception-redaction boundary.
Its descriptors still include caller-supplied public coordinates and keys.

The selected
[interpreter boundary](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py)
defines DeploymentProgram and interpretation errors separately. Its preparation
entry applies current policy authorization, validates an inline graph and composes
existing command services; the saved branch instead delegates saved preparation
and uses the expected selected lineage. That interpretation can write durable
control-plane state even though these input values are pure. This companion does
not review the full interpreter/projection workflow or infer that constructing
Progress invokes an implemented progression method.

The fully read
[general command suite](../../tests/test_deployment_program.py.md)
checks six export identities, the Prepare | Progress union, three constructed
values' no-__dict__/extra-assignment behavior, exact field lists, selected bounds/
nominality/coupling, readiness sorting/duplicate rejection and repr/descriptor
canaries. Its finite AST imports/call-name exclusions and two reverse-import checks
do not prove a complete transitive effect-free DAG. SavedDesiredTopologyRevision
appears in its export list but is not constructed there.

The separately fully read
[saved preparation value test](../../../../../control-plane-kit-operations/tests/test_saved_preparation_values.py)
does construct draft-a revision one. Eight invalid draft/revision pairs cover empty,
oversized and newline draft text and bool/zero/negative/string/overflow revisions.
It constructs a saved Prepare command with present current/desired lineage and
revision one, rejects absent desired lineage/revision zero and desired revision
overflow, checks the desired value is retained, and checks title/comment/operator
canaries are absent from repr plus descriptor.

That separate test does not assert every exact-type/lineage-string bound, successful
maximum revision, error chaining or frozen/slots property. Its imported principal
helper constructs a synthetic operator with all current policy scopes by default;
it does not authenticate a real caller. Selected saved-preparation fixture code
builds commands from actual selected draft/workspace records, but this slice did
not review the full saved-preparation integration suite or execute its setup.
The source coupling rules above are implementation facts, not coverage claims
inferred from helper availability.

Read depth: the complete 226-line owner and every helper/export were refreshed,
with full general suite472 and Core identity owner retained. The complete separate
saved value test, selected saved-fixture command construction/principal helper,
selected readiness/key/lineage validators and interpreter authorization/initial
preparation boundary were inspected. No full interpreter, saved integration,
admission/workflows/records or transitive dependency review is claimed. Validation
was documentation-only: local links, whitespace and frozen-source comparison. No
application imports, tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
