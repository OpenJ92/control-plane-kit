Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/observed_state.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/observed_state.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 180-line adapter stores observation evidence and queries it by workspace,
subject and observation time. PostgresObservedStateStore exposes put, latest,
latest_for_workspace, latest_page and history. Its private decoder reconstructs
typed ObservationRecord values. This store does not run probes, contact providers,
resolve graph topology, compute freshness at the current time or mutate graph
pointers. A stored HEALTHY/FRESH value is recorded evidence, not a live guarantee.

The constructor retains a caller-supplied PostgresConnection. The actual
[store bundle](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
binds this adapter to the same connection as the other stores, and the
[Postgres facade](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/__init__.py)
exports its class. No method opens, commits, rolls back or closes a connection.
With [PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py),
the caller requests commit for the entire grouped operation; normal successful
context exit commits, otherwise rollback/close follow that owner's rules. A direct
autocommit connection instead has its configured statement transaction behavior.
There is no hidden multi-statement transaction wrapper or retry loop in this store.

put issues one INSERT into cpk_observations for ID, workspace, subject, status,
observed_at, evidence, recorded freshness, graph/probe correlation and endpoint
context. Enums become their value strings, optional enum fields become SQL NULL,
evidence becomes Jsonb(record.evidence.descriptor()), and the timestamp passes
through the actual [Postgres temporal codec](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py).
Argument construction encodes time before execute is invoked, so malformed canonical
UTC text can fail without attempting the INSERT. The method returns the supplied
record rather than rereading database state or synthesizing an observed success.

This is an append path: there is no UPDATE, UPSERT, conflict-ignore, replacement,
delete, retention cleanup or automatic compensation method. The
[current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has a global observation_id primary key, workspace foreign key and a unique
observation/workspace pair used by effect-outcome association. Repeating an ID is
not an idempotent replay API here; a conflicting insert can raise the database
error and the caller owns rollback. The store alone neither proves all callers
are append-only nor offers safe recovery from an ambiguous commit.

The same table carries status/freshness/probe/endpoint vocabulary and correlation
constraints. graph_id is correlation data; this adapter does not fetch that graph,
prove it is current or require subject_id to name a node in it. Stale or historical
graph references can therefore remain useful observation evidence. It does not
validate actor permissions or provider provenance before insertion. The write
boundary assumes the calling owner has established the authority to record evidence.

The imported [ObservationRecord and BoundedEvidence](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
provide different guarantees. ObservationRecord requires typed status/freshness,
bounded text coordinates and BoundedEvidence, optional typed probe/endpoint values,
and either all or none of graph_id/probe_kind/probe_outcome. Process/readiness probes
cannot claim endpoint context, and kind/outcome combinations use the Core probe
contract. It does not itself parse observed_at as a timestamp or infer status from
probe_outcome. put accesses the supplied record's fields; it does not add an exact
ObservationRecord type check or rerun every constructor guard on hostile objects.

BoundedEvidence stores canonical JSON text and returns a fresh decoded descriptor.
It bounds encoded bytes to 4096, nesting depth to four, container fields/items to
32 and string values to 512 characters, rejects unsupported/nonfinite values and
selected secret-shaped keys. Those checks are structural input controls, not proof
that arbitrary allowed text is nonsensitive. In particular URL evidence is allowed
in storage and remains present in raw store reads; redaction belongs to projections.

latest(workspace_id, subject_id) selects only that workspace and subject, ordering
by native observed_at descending then observation_id descending and limiting to one.
It returns None when no row matches. Equal-time ties therefore use the observation
ID under the database's text ordering, not insertion order. It does not prefer a
healthy, fresh or current-graph observation over a later stale/unhealthy/historical
one, and it does not independently reject a nonexistent workspace.

latest_for_workspace filters workspace and uses DISTINCT ON (subject_id), ordered
by subject ascending then observed_at and observation_id descending. The result is
one selected latest record per subject in subject order. This legacy whole-workspace
method calls fetchall without a LIMIT; its output cardinality is not bounded by
the paged read contract. It is not a global observation-time ordering and does not
guarantee that every graph node has a corresponding observation.

history filters both workspace and subject, orders observed_at then observation_id
ascending and returns all matching decoded rows. It also has no LIMIT. Empty
history or an empty whole-workspace result means no matching recorded rows, not
that a workspace exists, all providers were inspected or no effects occurred.

latest_page is the bounded public-read adapter. It requires collection
LATEST_OBSERVATIONS. The actual [page language](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
assigns that collection WorkspaceReadScope, IdentityReadCursor and ascending subject
identity order, with maximum page size 100. Normal ReadPageRequest construction
checks the exact scope/cursor family, matching collection/scope and a positive
exact-int limit within the collection bound. The store relies on those value
contracts and adds its own collection check; it is not an independent parser for
arbitrary request-shaped input.

Without a cursor the SQL parameters are workspace and limit+1. With a cursor the
query adds the exclusive subject_id > cursor.item_id predicate. It retains the
same DISTINCT ON/latest-per-subject ordering and returns at most limit+1 selected
rows. Values are bound parameters; the interpolated seek fragment is fixed code.
Each row becomes an ObservationRecord and a candidate whose identity cursor uses
row[2], the subject ID, rather than the observation ID or timestamp.

ReadPage.from_candidates exposes at most limit items. If there is one extra
candidate, next_cursor refers to the last exposed subject; otherwise it is None.
All fetched rows, including the extra candidate, are decoded and given typed cursors
before trimming. Malformed extra-row evidence can therefore fail the page rather
than be silently ignored. Page construction checks candidate count and cursor
congruence; it does not sort or independently compare every candidate's ordering.
The SQL ORDER BY supplies that ordering. SQL row limits bound returned candidates,
not a universal bound on database work across all retained observations.

Subject-position pagination is live, not a cross-call snapshot. If a newer record
arrives for a subject already behind the cursor, the ongoing traversal does not
return that subject again. A subject ahead of the cursor can be returned with its
newer record. A fresh traversal can observe both updates. Timestamp ties have a
deterministic selected ID, but this store makes no cross-page transaction or
monotonic-provider-state guarantee. The supporting index orders workspace, subject,
observed_at DESC and observation_id DESC; no query-plan/performance claim follows
from merely documenting that index.

_observation_record decodes native aware timestamps, converts required and optional
enum strings and rebuilds BoundedEvidence from stored JSON, then constructs the
record. Malformed enum, evidence or correlation can fail there. Errors are not
translated to a single ReadModelError or sanitized catch-all in this module; callers
must preserve the appropriate boundary. Direct reads return typed raw evidence,
not a universally safe external descriptor. No decoder silently repairs graph
identity, drops malformed evidence or substitutes a success status.

The actual [read projection](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/observations.py)
first requires workspace existence, samples one aware clock and maps the page to
public observation descriptors. It computes freshness from recorded staleness,
graph correlation, timestamp validity/future position and configured age, without
changing the durable record. Its [redaction helper](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/_redaction.py)
masks selected secret/address/environment keys. This is where a stored URL becomes
<redacted>. Workspace predicates in all four store queries establish data selection;
projection existence checks and caller authentication/authorization are separate.
The adapter does not accept a principal or granted scopes.

The complete [read-service suite companion](../../../tests/test_read_services.py.md)
describes selected PostgreSQL evidence for latest-per-subject selection, URL
redaction, graph-change/recorded-stale/expiry/future freshness, malformed-time write
rejection and correlated-record guards. Its no-graph-rewrite assertion uses a
pre-read workspace value; it is not a full post-read invariance check. Its temporal
boundary is exactly five minutes versus one microsecond later. No fresh test run,
provider probe or deployment acceptance is claimed by this store companion.

Selected [current-metadata page tests](../../../../../../control-plane-kit-operations/tests/test_current_metadata_read_pages.py)
add two distinct kinds of evidence. A recording connection returns no rows and
checks SQL DISTINCT ON, workspace/subject seek, order, one execute call and limit+1
parameters; that is query-shape evidence, not a database selection test. The real
PostgreSQL replacement case starts with subjects b/d/f, reads b/d, appends newer
b/f records, then sees only new-f on the continuation and new-b/old-d/new-f on a
fresh traversal. Its helper follows cursors until None. The fixture uses a private
schema and drops it afterward; it does not simulate concurrent transactions.

Selected [native temporal ordering assertions](../../../../../../control-plane-kit-operations/tests/test_native_temporal_ordering.py)
store one observation at an exact second and two at the next microsecond. latest
and latest_for_workspace select the later z ID, while history returns earlier,
later-a, later-z. These exercise native-time ordering and the documented ID tie
break in an actual test database. They do not cover every collation, cursor branch,
retention scale or malformed-row class. Neither supplementary suite was read in
full for this note.

Read depth: complete 180-line owner and every SQL/decoder path; complete 1,421-line
read-service suite and observation projection/redaction modules from the immediately
preceding assignment; previously complete UoW/temporal reads. Selected actual record/
evidence/probe guards, identity request/cursor/page construction, bundle/export
binding and observation schema constraints/index were checked. Supplementary page
query/replacement tests, traversal/setup helpers and native-time selector assertions
were read only at the named scope. No full records/read-pages/schema/Core probe or
supplementary test-suite audit is claimed. Static documentation validation covers
links, whitespace and frozen source/tests; no executable imports/tests, database/
provider calls, credential access, source/inventory edits, publication, merge or
live work were performed.
