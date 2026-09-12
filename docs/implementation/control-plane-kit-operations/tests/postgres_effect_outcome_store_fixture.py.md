Source: [control-plane-kit-operations/tests/postgres_effect_outcome_store_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_outcome_store_fixture.py).
Maintain this document alongside its source file. When setup, persistence, outcome
or imported fixture contracts change, verify and update this companion in the same
change.

This 297-line support module composes synthetic outcome values with actual PostgreSQL
stores. It has no test methods. Consuming suites supply unittest assertions and run
the setup/helpers. It is not a provider execution, recovery-policy or coordinator
fixture: direct record insertion creates the selected database preconditions.

PostgresEffectOutcomeStoreFixture inherits the
[pure outcome fixture](effect_outcome_evidence_fixture.py.md) and
[lease recovery fixture](execution_lease_recovery_fixture.py.md). It explicitly calls
the latter's setUp and tearDown, rather than relying on cooperative super calls.
The selected actual parent setup requires CPK_OPERATIONS_TEST_DATABASE_URL, fails
rather than skips when absent, opens an autocommit psycopg connection, installs the
current schema and truncates cpk_workspaces CASCADE. Teardown truncates again and
closes if that connection remains open. These are destructive test-database setup
operations, not authority to inspect or clear a developer/provider database.

setUp then calls seed_truth(RENEW_ACTIVE_CLAIM, history="active-empty"). This name
selects fixture data; it does not execute a recovery command. The parent inserts
workspace-a separately under autocommit, groups empty identity graphs, a session
and a one-StartRuntime plan in one unit of work, then groups approval, request, run
and initial history in another. Claimed request/run values, worker-a/generation
seven and fixed 2098/2099 lease dates are seeded, not established by a worker claim.
active-empty contains RUN_OPENED only, not a started/executed activity journal.

The selected [graph helper](graph_lineage_fixture.py.md) saves empty authored graphs
and their identity realized projections. The outcome stories later use synthetic
activity-a/StartNode or StopNode intent values, not the seeded plan's start-runtime
activity. This setup supports store relationships, not plan-to-effect membership
or an end-to-end execution proof. A successful insert must not be described as
approval, authority delivery, provider success or full journal validity.

The module dynamically imports the outcome-store owner. Only a missing exact target
module returns None; a nested missing dependency propagates. require_store asserts
the exported class is present. Direct imports of outcome records/projections and
other fixtures remain normal collection dependencies; no complete apparatus fallback
is provided.

story_named selects a named row and exact bool phase from the inherited twenty
stories; a missing match raises StopIteration. record_for constructs the actual
outcome wrapper, calls the production effect_outcome_observation_records helper,
then constructs EffectAttemptOutcomeRecord. This deliberately reuses the production
projection to make valid persistence inputs. It is not an independent expected-row
oracle; the pure fixture's separately constructed expected observation rows serve
that role in other tests.

indexed_record makes ordinary-phase rows for paging/identity tests. It assigns
activity-<index:03d>, page-specific start/direct IDs, original ordinal 10+2*index and
latest ordinal 11+2*index. It recomputes the request fingerprint for the new identity,
changes the value's effect ID and, for observation values, its request fingerprint,
then builds a coherent direct snapshot. Default observed-absent has no observation
rows. Other named stories can carry endpoint rows with one-based page-specific IDs;
callers may supply an exact tuple of alternate IDs to exercise association constraints.

The three-digit formatting is a minimum width, not an index-range or count bound.
This helper does not validate arbitrary indexes independently: underlying identity,
event and record constructors own rejection. indexed_empty_record simply selects
the default observed-absent form. Pagination order and cross-record uniqueness must
be tested by consumers, not inferred from these convenient names.

retry_record returns two values without writing: a prior STARTED attempt-one record
and a succeeded direct outcome for attempt two with an immediate-prior pointer.
Both refer to the same run/activity and synthetic fence. Start/direct ordinals are
five/seven, the prior start is three, and two retry observation IDs are fixed.
This is a retained-attempt relation fixture, not approval to blindly retry an
ambiguous provider action or proof the prior attempt was settled by a workflow.

preimage_for serializes the raw execution result or observation descriptor with
RFC8785. It returns bytes, not the public summary descriptor or a fingerprint, and
does not add a separate size or redaction check. These bytes can contain the
fixture's endpoint addresses, provider messages and synthetic detail canaries.
Their equality establishes representation retention, not safe public disclosure.

persist_prerequisites opens one unit of work and adds original/latest events, then
conditionally inserts intent evidence when the bundle exposes effect_attempt_intents.
add_record_intent delegates directly to the
[attempt-store fixture](postgres_effect_attempt_store_fixture.py.md): derive phase
from the original event kind, build actual intent evidence unless supplied, assert
its request fingerprint equals the attempt's and assert the intent store's return.
The conditional hasattr is fixture compatibility behavior; it is not proof every
possible injected store bundle enforces intent presence.

Next it asserts insert_absent returns the supplied attempt and observed_state.put
returns each observation. Event-write returns are not asserted here. It requests
commit only after all those calls. The actual
[unit of work](../src/control_plane_kit_operations/postgres/unit_of_work.py.md)
physically commits on successful context exit; otherwise rollback/close follow its
rules. Returned local records alone are not commit acknowledgments or readback.

persist_outcome first completes persist_prerequisites, then opens a separate unit
of work for effect_outcomes.insert and commit. Thus failure of outcome insertion
does not roll back the already committed prerequisite transaction. There is no
outer transaction, automatic compensation, cleanup or retry loop. This helper is
not atomic across the full attempt/events/observations/outcome set, nor does it
claim to reproduce the grouped atomic-fold owner.

The selected actual [outcome store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
reconstructs/admit-checks the record, enforces an encoded preimage size of 1..8192,
derives request/workspace membership through the run, inserts the outcome and
zero-based ordered observation-membership rows, and returns the supplied record.
It does not commit independently. Repeated insert is not a silent idempotent replay;
database conflicts may escape. get uses exact attempt identity and direct-event ID,
decodes retained preimage/state/events/memberships and reconstructs a historical
direct record rather than substituting today's current attempt snapshot.

recover_current_attempt intentionally changes current attempt truth after a direct
outcome has been stored. It builds a synthetic SUCCEEDED recovery decision with
fixed decision-later-recovery, repeated-f fingerprint, ordinal one after the direct
event and fixed later timestamp. It preserves the original start, identity, request,
fence and prior pointer; phase selects the recovered-success event kind. In one unit
of work it adds the event, asserts compare_and_set(current,replacement) returns the
replacement and requests commit. It does not call a recovery service, check actor
approval, obtain provider evidence or update the retained direct outcome row.

The selected [store tests](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store.py)
check prerequisite identities and later readback; all twenty direct stories roundtrip
through fresh unit-of-work connections and compare stored preimage bytes. Despite
after_restart in their names, these methods do not restart a process or PostgreSQL.
Each story resets fixture truth before insertion; shared story IDs are not a global
unique-ID allocation algorithm.

The later-recovery case changes an uncertain result's details, stores it, applies
the synthetic current replacement, then requires the historical read to retain
the original uncertain status/fingerprint/details with no recovery decision. The
attempt-two case explicitly persists the prior, intents, events, observations and
outcome together in one unit of work, unlike persist_outcome's two transactions.
It verifies exact roundtrip and immediate-prior linkage, not provider retry safety.

The selected duplicate/no-commit test expects a raw UniqueViolation on reinsertion.
After resetting, it commits prerequisites, inserts an outcome without requesting
commit, then checks outcome and membership table counts are zero. It does not claim
all prerequisite tables are empty, test a post-commit lost acknowledgment or simulate
a service crash. The larger codec, paging, malformed-data and HTTP-verification test
matrices were not fully reviewed for this companion.

Security and scope: seeded actors, approvals, fences, authority-reference values,
fingerprints and private endpoint text are synthetic data, not credentials or
authorization proofs. Raw preimage storage is distinct from bounded public summary
projection and redacted reads. Never render raw fixture values into public reports.
No provider, Docker socket or external network call is made by these helper bodies;
their PostgreSQL mutations belong only to the owning Docker-backed test suite.

Read depth: full 297-line fixture; full pure outcome736/record-parent378 retained;
actual selected lease setup/teardown/UoW/seed/active-history path, intent insertion,
graph seeding and outcome-store insert/get/admission/serialization/reconstruction;
full UoW102 and named consuming test sections. The complete lease/attempt-store
fixtures, 742-line outcome store, schema, Core and consuming suites were not audited
in full here. Documentation-only validation: no application imports, executable
tests, database/provider/credential calls or source changes. Independent review
must precede inventory promotion and publication.
