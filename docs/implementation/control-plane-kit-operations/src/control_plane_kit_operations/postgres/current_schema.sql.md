Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql).
Maintain this document alongside the DDL. Recheck the semantic contract, verifier,
installer and affected store/record boundaries when relations or constraints change.

This 1,399-line file is the direct creation program for the current 40-table
Operations schema. It declares tables, checks, primary/unique keys, foreign keys and
indexes. It has no migration ledger, conditional create, data backfill, drop/truncate,
row mutation, trigger, policy or privilege-management statement. Its ALTER statements
add constraints to the newly created relations; they are not a historical upgrade
sequence. Re-executing this SQL against an existing schema is not its idempotency
mechanism. The installer decides whether creation or verification is appropriate.

The full DDL, full 105-line installer, full 627-line catalog verifier and full
384-line current-row validation owner were read. The 1,157-line semantic contract
was inspected at selected depth: its four record shapes, representative relation
facts, and relevant run/secret-authorization columns, constraints and indexes.
The full records owner was previously inspected for its own companion. Selected
installation/outcome-schema tests and outcome-store reconstruction/scan helpers were
read as described below. This is not a full review of every store, every contract
entry, all test fixtures or a running database. No SQL, import or test was executed.

[schema.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
loads this file as package data through importlib.resources. install_schema requires
a connection exposing a real bool autocommit attribute and a transaction context.
Inside that context it takes an advisory transaction lock derived from database and
current_schema(). If the namespace is object-free, it executes the DDL, takes SHARE
locks on the expected relations and verifies structure plus retained-row semantics.
For a nonempty namespace it requires expected ordinary tables, locks them, and
verifies instead of issuing corrective DDL. A mismatch requests reset through a
bounded error; the installer does not perform that reset. Other failures become a
generic installation error. Transaction completion/outer-transaction ownership is
provided by the supplied connection context, not BEGIN/COMMIT statements in this file.

The SQL uses unqualified relation names and does not create a namespace. The
installer/verifier operate relative to the connection's current namespace. This
file does not choose a database, configure credentials or establish resource
ownership for a live environment.

The frozen
[semantic contract](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
describes relations, ordered columns, constraints and indexes, including more than
their names: types/defaults/collations, validation and deferral flags, composite
references, predicates, index key/include entries and access properties. The
[catalog verifier](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_verification.py)
compares bounded candidate sets against those values and requires four true results.
Candidate limits use expected counts plus one to detect excess. It also checks
namespace adjuncts and rejects user triggers/policies/rules on expected relations.
This is exactness for its recorded semantic surface, not a checksum of DDL text or
a complete audit of database roles, privileges, storage settings and server policy.

Most identities are application-supplied text; ordinals/attempts/versions use integer
or bigint rather than generated sequences. Regex/length checks are specific to
columns and are not a universal text grammar. Many status and command fields have
closed CHECK vocabularies. Timestamps are PostgreSQL timestamp with time zone,
sometimes with explicit precision 6; DDL does not enforce the textual UTC encoding
used at the Python transport boundary. Generic metadata, graph and payload JSONB
columns do not all receive object, size, secret-key or canonical-content checks.

The workspace/graph family consists of cpk_workspaces, cpk_graph_versions and
cpk_realized_graph_projections. Graphs have global graph_id and workspace/version
uniqueness. Realized projections have global projection_id and a semantic unique
identity `(workspace_id, source_authored_graph_id, projection_kind, projection_key)`.
Composite keys also expose projection/source and projection/workspace relationships.
A projection's authored source must belong to the same workspace. Workspace current
and desired authored/projection pointers must be both null or both present; two
composite references per pair bind the source graph and owning workspace.

This is a nullable cycle: a workspace can be inserted without graph pointers, then
graphs/projections can reference it, then the workspace can point to them. These
foreign keys are not declared deferred. Projection kind/digest spelling is checked,
but SQL does not recompute the projection digest or decode graph material. The
previously inspected records and graph store own stronger canonicality and semantic
identity checks.

Desired topology drafts, draft revisions and saved preparation sources add durable
selection history. A draft's `(workspace_id, draft_id)` key owns a positive head
revision, bounded nonblank title and paired deletion actor/time. A revision's key is
`(workspace_id, draft_id, revision)` and its graph belongs to the same workspace;
workspace/graph uniqueness prevents sharing one authored graph across revision rows.
The draft-to-head-revision foreign key is DEFERRABLE INITIALLY DEFERRED, while the
revision-to-draft reference is immediate. This permits construction of the aggregate
inside one transaction, with the head relationship checked at its deferred boundary.
It does not enforce contiguous revision numbers or immutable history by itself.
cpk_saved_preparation_sources allows one source per session and binds its workspace
to both the session and exact retained draft revision.

The session/plan/approval family stores grouped intent and the material reviewed for
execution. Sessions have open/closed/cancelled status and matching closed-time
presence; actions have positive session-local ordinals and closed command names.
Nullable idempotency keys have scoped partial unique indexes. Plans retain base and
desired authored/projection pairs, a nonnegative desired revision, and closed status.
Their composite projection references prove the selected authored sources; the plan
table has no workspace column that independently binds those graphs to the session's
workspace. Current-row validation supplies that additional join check.

Approval requests distinguish an activity-plan subject from a gateway-key-rotation
subject by exactly one nonnull plan_id/rotation_id. They retain subject JSON,
review-digest spelling, scope, risk and destructive flag. Approval decisions have
one row per request and a decision/request candidate key. These constraints do not
derive the review payload/digest from the target, derive risk, or authenticate the
approver. Execution requests bind decision to approval request, plan to session,
and session to workspace through composite foreign keys. They do not thereby prove
that the approval subject is that plan or that its decision permits execution.

Execution requests have queued/claimed/cancelled/abandoned status; claim worker,
generation, claim time and expiry are all present only for claimed requests.
Generation is positive bigint. An active-plan partial index permits only one queued
or claimed request per plan, and workspace/idempotency is unique. No DDL check
establishes lease expiry against current time or compares expiry with claim time.

Activity runs bind a request/plan pair, have request-local attempt uniqueness and a
partial unique active-request index for claimed/running/paused/compensating states.
Retry attempt 1 has no prior run; later attempts require a different prior run ID
that exists. That self-reference does not independently prove the prior run used the
same request or that its attempt number was the predecessor. Run IDs use the
canonical ASCII regex. Status checks enforce start/settlement presence. A concrete
difference from ActivityRunRecord is that this DDL requires started_at even for
cancelled runs, whereas the record constructor permits a cancelled run without it.
Neither presence rule establishes chronological ordering.

cpk_activity_events has a global event ID, unique run/ordinal and an event/run/ordinal
candidate key for stronger references. Its checks require activity_id for step kinds,
forbid it for run kinds, and restrict recovery-object placement to the recovery
decision kind. They do not reconstruct lifecycle legality, require gapless ordinals,
validate all nested evidence, or reproduce every record-level failure/recovery law.
Current-graph advancement events/actions and timeline indexes support history queries;
an index name is not evidence that an advancement was authorized or completed.

Execution command receipts are keyed by `(run_id, idempotency_key)`. They bound the
key, worker text, claim generation, scope-array count/JSON size and initial/result
JSON sizes; max_effects is positive canonical decimal text. Status requires either
an incomplete receipt without result/time or a completed receipt with both. SQL
checks fingerprint spelling, but does not recompute command intent, validate every
scope member/order, compare completion time to admission, bind result run lineage,
or enforce the result's effect budget. Those stronger checks belong to receipt
construction and store/coordinator behavior, not this two-shape CHECK.

The effect family separates protected intent, current attempt state, direct outcome
and outcome-observation membership. All use `(run_id, activity_id, attempt)` as the
attempt identity. Intent rows bind run/request and request/workspace, a start-event
triple, a request fingerprint and a 1-byte through 1-MiB preimage. Attempt rows
reference the intent's identity/fingerprint/original-event commitment, retain a
worker fence, and reference original/latest event triples. Prior attempts must be
the previous number for the same run/activity and exist. Started state keeps the
original event as latest; later states require a greater latest ordinal. Recovery
fields have all-or-none and status/fingerprint consistency checks.

These foreign keys do not hash intent bytes or prove that an event's JSON carries
the same activity/effect meaning. Intent rows have no reverse foreign key requiring
an attempt row; attempt-to-intent admission is immediate, not a deferred two-way
aggregate. Stronger protected-intent/attempt reconstruction belongs to its owners.

Direct outcomes retain execution-result or provider-observation profile, a preimage
of 1 through 8,192 bytes, fences/fingerprints, direct terminal-or-uncertain status,
original/direct event triples and observation_count from 0 through 8,192. They bind
attempt identity, run/request and request/workspace, plus both event triples. A
direct event is unique across outcome rows. The DDL does not compare every retained
outcome field against the current attempt snapshot or decode/recompute its preimage.

Membership rows carry the same attempt, workspace and count, a zero-based position,
and observation ID. The primary key makes positions unique per outcome; the check
requires `0 <= position < observation_count`. A composite foreign key binds that
count/workspace to the outcome and is DEFERRABLE INITIALLY DEFERRED. The observation/
workspace reference is immediate. Observation ID uniqueness is separately DEFERRABLE
INITIALLY IMMEDIATE. This permits controlled aggregate write ordering but does not
require exactly observation_count rows or prohibit missing positions. No reverse
reference or count trigger supplies that completeness law. Selected
[outcome-store helpers](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
instead compare the fetched membership count and ordered positions during decoding;
its current-row scan fetches count + 1 and delegates to reconstruction.

Failed-run compensation uses one program per run, unique associated action/event,
and ordered source steps. The program binds request/workspace, run/request and
plan/session, requires reason post-effect-failure, four digest spellings and a
bounded program preimage. Each step references a source direct outcome and completion
event, with base-graph or desired-graph material. Attempt bindings reference the
exact source step and a unique inverse attempt, requiring same run/activity and
source + 1 without integer overflow. These relations retain admission and linkage;
they do not establish that the JSON operation is the correct inverse, recompute the
program commitment, enforce contiguous steps, or perform compensation.

Registered products retain descriptor text, JSON and its digest. Unlike most digest
columns, descriptor_sha256 has a SQL check recomputing SHA-256 from UTF-8
descriptor_content. Workspace/digest is unique. This does not independently prove
that descriptor_document is the semantic decoding of that text. Product, image-pull,
runtime and ingress authority registrations also retain active/revoked status and
workspace ownership. Runtime authorities close authority kind to local socket or
remote TLS; ingress closes provider kind to Cloudflare. Runtime deliveries retain
typed JSON and secret-reference arrays. Partial indexes enforce selected active
reference identities, but delivery/authority_ref text is not itself a foreign key
to the corresponding runtime-authority row.

Secret providers, references and use authorizations store custody coordinates and
admission history. Providers and references have workspace-scoped active uniqueness,
supersession links and revoked actor/time consistency. References bind their provider
within the same workspace. Use authorizations bind both provider and reference
registration to their workspace, close use_intent vocabulary, and have unique
workspace/correlation and authorization/workspace keys. Optional operation/session/
run/activity/effect/probe coordinates receive spelling checks but no corresponding
history foreign keys in this table. The two provider/reference foreign keys alone
do not prove that a use row's provider is the provider selected by its reference row.

There are actual inbound references to cpk_secret_use_authorizations: the transit
and workload authorization/workspace foreign keys from cpk_node_control_attempts.
Any prose describing this table as having no inbound references is stale. Node
control attempts also bind current realized/source graph and workspace, both signing
key registrations and workspace, and unique request and issuer/JTI identities.
They retain request bytes and two grant byte strings with separate size ceilings
and digest spelling. SQL does not verify signatures or recompute those digests.
The schema therefore cannot be described as containing only harmless reference
strings: signed grants and protected preimages also need controlled storage/read
boundaries. No encryption or general output redaction is implemented by this DDL.

Delegation signing keys retain public material plus a private-key reference, closed
purpose/algorithm/status and lifecycle evidence pairs. A partial unique index permits
one active key per workspace/purpose/issuer; verifier-set lookup includes active and
verify-only keys. Gateway rotations, transitions, deployments and revocations retain
the rotation's versioned workflow and checkpoints. Transition versions advance by
one and to-version is unique per rotation, but status membership checks do not prove
that a particular from/to transition is legal. Deployment acceptance requires its
accepted graph/projection/time fields; those many retained deployment coordinates
are not all foreign keys to execution/graph tables. The child rotation tables do
reference their rotation parent.

Gateway probe attempts retain request, graph, gateway/key/grant coordinates, epoch
times and result evidence, with unique workspace/request and grant JTI. They reference
workspace and graph individually, not as a composite proving the graph's workspace.
Their status/completion checks and positive lifetime relation do not prove a valid
signature, authorized route or successful remote probe.

Cloudflare ingress resources retain workspace/ingress/epoch identity, provider IDs,
hostname, lifecycle, source coordinates and removal evidence. The partial active
identity covers allocating, active and removing; it excludes uncertain and orphaned.
Removed status requires removal time/run, while other statuses forbid those fields.
Generated ingress secret references retain token-purpose reference strings and source
coordinates with workspace/reference uniqueness. These tables reference workspace;
their run/event/provider coordinates are not all enforced relational links. They
neither prove provider ownership nor authorize teardown of the named resources.

Observations retain separate workspace/subject/status/time/evidence/freshness and
optional correlated graph/kind/outcome. Correlation requires all three or none;
process/readiness prohibit endpoint context. Kind and outcome are individually
closed, but SQL does not enforce the complete kind/outcome pairing matrix, derive
freshness, or couple ObservationStatus to ProbeOutcome. graph_id has no graph foreign
key here. Membership joins can bind an observation's workspace to an outcome, but
ordinary observations have no blanket outcome/run ownership requirement.

Across these families, foreign keys declare no cascading delete/update action. They
preserve references rather than providing an application cleanup algorithm. The
two deferred foreign keys and one deferred-capable unique constraint described above
are the explicit deferral points; other nullable cycles are handled by row shape and
write ordering. Tables have no mutation-prevention triggers, so terms such as
immutable history describe service/store contracts rather than a universal SQL
prohibition on UPDATE or DELETE by a suitably privileged caller.

[current_data_validation.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py)
adds selective retained-row checks during installation/verification. It scans authored
graphs referenced by workspace pointers, plans or draft revisions in bounded batches,
reconstructs values, and checks identity-projection collisions. An absent matching
identity projection returns False from its observation helper, which the scan does
not treat as failure; this is not automatic projection creation or a requirement
that every authored graph have an identity row. Separate queries check workspace/
plan lineage and approval subject material/digests, and delegated scans validate
attempts, intents, outcomes and saved preparation sources. This is not semantic
validation of every row family in the schema.

Another concrete vocabulary limit belongs to that validator: the rotation-subject
query admits three purposes, while the DDL's rotation/key purpose checks also admit
gateway-node-control-transit. This note records that difference; it does not claim
end-to-end approval support for every DDL-admitted purpose. DDL, record constructors,
stores and retained-row verification must be reviewed together when changing a law.

Selected governing tests provide bounded supporting context:

- [test_current_schema_installation.py](../../../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
  has a static direct-program case checking statement families, forbidden historical/
  mutating constructs, 40 CREATE TABLE statements and exact SQL bytes. Inspected
  database cases cover empty creation, query-only reentry, injected install rollback,
  caller-owned outer rollback and two concurrent installers. The selected fixture
  creates a unique schema on the package test database and drops it in teardown.
  These tests were read, not run; the complete large suite was not inspected here.
- Selected [outcome schema cases](../../../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_schema.py)
  assert contract counts/columns, exact check failures for empty/oversized preimages,
  excess count, started direct status and out-of-range positions, and named composite
  foreign-key failures for changed ownership/event/count fields. The single-member
  case tests the position bound, not aggregate completeness by SQL. Its imported
  PostgreSQL fixture and selected setup delegation were inspected; no full fixture
  or provider execution coverage is claimed.

The real owning package suite is required for executable schema evidence. This
documentation-only pass supplies no installed-schema observation, migration proof,
provider effect or live cleanup result, and adds no new security or durable-data
surface.
