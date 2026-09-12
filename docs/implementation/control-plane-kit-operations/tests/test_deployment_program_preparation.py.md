Source: [control-plane-kit-operations/tests/test_deployment_program_preparation.py](../../../../control-plane-kit-operations/tests/test_deployment_program_preparation.py).
Maintain this document alongside its source file. When the test or relevant
service contracts change, verify and update this companion in the same change.

This 519-line suite contains seven PostgreSQL-backed tests of inline deployment
preparation. It exercises actual Operations services, persisted stage boundaries,
replay and selected rejected-state outcomes. It does not exercise saved catalogue
preparation, progression, provider execution or a running deployment. The returned
preparation projection describes where the program stopped; approval-required is
not an approval decision or evidence that runtime work occurred.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL and raises an apparatus error when
it is absent. It opens an autocommit psycopg connection, installs the current schema,
truncates cpk_workspaces CASCADE and initializes a shared program-N identity counter.
tearDown truncates again and closes the connection in finally. Several subtests
also reset the workspace tables. These are destructive fixture operations intended
for the owning isolated Docker-first Operations apparatus, not a shared database.
This documentation pass did not run setup, imports, tests or database commands.

unit_of_work supplies a fresh psycopg connection for each PostgresUnitOfWork.
services builds OperationCommandService, DesiredGraphCommandService,
ActivityPlanningCommandService and ApprovalCommandService with that factory,
fixed per-stage timestamps and the same advancing identity factory. program creates
a new interpreter from those services, or an injected tuple of replacements.
module dynamically imports the interpreter and reports its own missing module as
a test failure; a nested ModuleNotFoundError is reraised rather than mislabeled.

context constructs a synthetic operator principal with a matching workspace grant.
Its default scopes are INSTANCE_WORKSPACE_EDIT and PLAN_REQUEST. It derives a
TrustedCommandContext through the identity API, but does not verify credentials.
setup_workspace creates a workspace, saves a current graph at version one, sets its
current pointer and commits those writes together. It returns the authored/current
realized lineage. command supplies an inline desired graph, that expected current
lineage, absent desired lineage/revision zero, title, parent idempotency key and
approval comment. No saved-preparation service is supplied by this fixture.

The actual
[preparation interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py)
authorizes the workspace-edit and plan-request scopes, validates the inline desired
graph, then calls four ordinary execute entry points in order: start session, set
desired graph, request plan and, when needed, request approval. It has no outer
transaction grouping these calls. This suite therefore exercises sequential
transactions, even though individual service writes are grouped within a stage.
The separate saved branch and caller-owned grouped preparation paths receive no
coverage from these tests.

The inspected
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
makes commit() a request; the physical connection commit happens on successful
context exit. Exceptions roll back the active stage, and exit closes its connection.
In the exercised
[session service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py),
execute wraps start_in_unit_of_work and commits session plus initial action together.
The inline interpreter uses execute, not that uncommitted inner entry point.

The selected
[desired and planning services](../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py)
own separate units of work and action-idempotency locks. Desired authoring checks
the expected desired coordinates under a workspace lock, saves graph/projection
truth and changes desired state alongside its action. Planning verifies an open
matching session and current/desired workspace coordinates, reads and validates
realized graph descriptors, derives the transition/plan and writes plan plus action.
Its fresh-plan authority-delivery admission reads durable registrations; it does
not call a provider. The selected
[approval service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/approvals.py)
writes a request plus action after checking plan readiness and request authority.
These service paths explain the partial stage counts; the tests do not independently
assert every lock, row field or lower-level rollback law.

The interpreter derives deterministic child keys for session, desired, plan and
approval from stage, workspace and parent key. Session metadata carries a separate
SHA256 commitment to preparation intent, including actor, desired descriptor,
expected lineage/revision, title and comment. Session replay checks its fingerprint;
the selected workflow fingerprint includes that metadata. Consequently changing
later-stage intent can conflict at the already committed session stage. Child-key
namespace and intent commitment serve different purposes.

Desired replay uses recorded action evidence and graph truth; planning replay
checks recorded plan/action linkage and rederives the plan from persisted graph
projections; approval replay checks request/action fingerprints. The matrix below
checks selected consequences of these paths, not exhaustive replay corruption or
concurrency behavior. Each call constructs new services but uses the same database
and shared test identity counter.

The scenario matrix selects five IDs from the
[Core planning corpus](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py):
no-change returns DeploymentNoChanges; unsupported-implementation-transition
returns DeploymentReviewBlocked; fresh-deployment, backend-switch and full-teardown
return DeploymentApprovalRequired. Each subcase prepares twice, requires equal
returned projections and unchanged counts across all eleven counted tables, then
requires one session, one plan, zero or one approval request and three or four
operation actions respectively. It checks both current lineage pointers remain
unchanged and all five progression tables remain empty.

Only those five of the corpus's eleven scenarios are selected. Their graph values
are consumed, but this suite does not assert the corpus's complete operation,
dependency or risk expectations. The selected graph helpers compile topology values
with synthetic implementation endpoints; DockerRuntime and full-teardown describe
planned values, not Docker/provider execution by this test. Projection equality
compares the returned reference and any approval-request ID, not every persisted
plan/session/action payload or a descriptor serialization.

The zero-activity case changes an empty graph's name from before to after. It
requires DeploymentApprovalRequired, reads the persisted plan and asserts its
activities equal (), requires one approval request and preserves current lineage.
This protects the distinction between a no-op transition and a graph update whose
plan has no activities. It does not assert the complete graph diff, approval risk,
or full absence of progression truth in this particular case.

The restart test loops over session, desired, plan and approval. FailBefore raises
the supplied sentinel before invoking a service; FailAfter invokes the real service
to completion and then raises that same object. Replacing the next service with
FailBefore stops after the preceding stage returned and committed. Approval uses
FailAfter because it is the last stage. The exception identity must be preserved.

For each stop point, fresh service objects prepare the original command and replay
it once more. The projections must agree and be approval-required. Final counts
must be one session, two authored graphs, one plan, one approval request and four
actions, with no rows in the five progression tables. This is recovery after
injected post-commit control-flow failures in one Python test process. It does not
kill a process, restart PostgreSQL, interrupt SQL/commit, lose a commit response,
reset the ID generator or prove rollback inside a stage. It does not snapshot each
intermediate stage or assert current-lineage preservation in this particular test.

The changed-intent test first stops before desired authoring, leaving the session
committed. Six mutations independently change actor, desired graph, expected current
lineage, expected desired lineage/revision, title or comment. Reusing the parent
key must raise DeploymentProgramStateConflict with the exact fixed message and no
cause/context. Counts must remain one session, one graph, one action, no plan or
approval request, and no progression truth. This establishes conflict before the
desired write for those six changes. It does not independently vary every command
field, check all fingerprint bytes or prove authorization-denial behavior.

The workspace-namespace case creates two workspaces with identical graph values
and the same parent key. Both preparations return no-changes, their plan IDs differ,
and the two session rows have the expected sorted workspace IDs and different
derived idempotency keys. It does not prepare either command again, test different
actors within one workspace or exercise competing concurrent calls.

The unavailable-state test has three subcases. Stale desired lineage/revision
leaves session/action/graph/projection counts (1, 1, 1, 1). The case named
missing-current supplies a different expected projection ID; it does not delete
the stored current projection. The malformed-current case directly replaces the
stored projection descriptor with invalid JSON shape containing GRAPH-CANARY.
Both latter cases leave counts (1, 2, 2, 2), reflecting the committed desired stage.
All three require no plan/request, unchanged current pointer coordinates and empty
progression tables. They require the fixed conflict message, no CANARY in str(error)
and no cause/context. They do not restore or compare the corrupted descriptor,
inspect every desired field, or establish universal exception redaction.

The metadata test queries the session metadata column only. It requires precisely
deployment_prepare_intent_sha256, a lowercase 64-hex value, and absence of title,
comment, desired graph name, actor, workspace and parent-key strings from its repr.
It does not recompute the digest or assert encryption/secrecy for all stored rows.
The session service separately stores title and actor, and the approval service
stores its comment when a request is made. The metadata assertion must not be
generalized to those independent columns or records.

_counts reads eleven tables through the autocommit inspection connection: sessions,
actions, authored graphs, realized projections, plans, approval requests, approval
decisions, execution requests, runs, events and observations. The five-table
no-progression helper covers decisions, execution requests, runs, events and
observations only. It does not count every Operations table, effect attempt, outcome
or provider resource. _assert_current_lineage hardcodes workspace-a and checks two
pointer columns, not graph contents, desired revision or whole-workspace equality.

Security and operational boundaries are explicit: all supplied principals have
preparation scopes, the tests request rather than grant approval, and no execution
adapter is provided. Fixed-message checks cover the enumerated state failures;
the actual interpreter deliberately passes InvalidOperationCommand through and
does not translate arbitrary SentinelFailure. No denied-scope, real authentication,
external readiness, provider, compensation, cleanup or live-history acceptance is
proven. Destructive database reset belongs only to the owning test apparatus.

Read depth: the full 519-line test, all its local helpers and full 301-line
preparation interpreter were read. Full PostgresUnitOfWork and selected session,
desired/planning, graph-authoring and approval execution/replay paths, three return
projection definitions, and the selected Core scenario factories/helpers were
inspected; prior command/identity reading was retained. This is not a full review
of those larger service/store modules, the full scenario corpus, saved preparation
or all projection variants. Validation was documentation-only: local links,
whitespace and unchanged inspected source against the frozen baseline. No source,
test, inventory, database, provider or credential mutation was performed.
