Source: [control-plane-kit-operations/tests/test_operations_table_atlas.py](../../../../control-plane-kit-operations/tests/test_operations_table_atlas.py).
Maintain this document alongside its test file. Recheck the atlas format and
current-schema contract whenever its accounting or dependency declarations change.

This 453-line unittest module contains six tests of the package-local
[Operations Table Atlas](../../../../control-plane-kit-operations/OPERATIONS_TABLE_ATLAS.md).
It checks documentation structure and selected semantic accounting against the
frozen schema contract. It does not connect to PostgreSQL, install a schema,
exercise stores, execute a restore or verify external resources. Its useful
guarantee is that the recognized table, foreign-key and cycle declarations track
the contract, with a small set of prose requirements enforced literally.

The actual [contract module](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
is loaded directly from its source path with importlib.util, registered under a
private name in sys.modules and executed by the loader. This avoids importing the
Operations package through its root, but it is still Python module execution when
the test runs. The loaded module supplies CURRENT_POSTGRES_SCHEMA_CONTRACT and its
SHA256 constant. The contract's frozen dataclass definitions and literal tuples
are the accounting authority; no database introspection supplies expected values.

PACKAGE_ROOT comes from the test file's location. _atlas_text requires the atlas
to exist at that root and reads UTF-8 text. There is no fixture-generated substitute
for the actual document. The synthetic _PARSER_WITNESS separately demonstrates
recognized header, table, foreign-key, graph and marker syntax. It is not a coherent
miniature database contract or an independent graph-algorithm test.

The first test requires exactly one recognized current-contract header. Its regular
expression fixes lowercase 64-character hexadecimal SHA syntax and ordered decimal
counts for relations, columns, constraints, indexes and foreign keys. Parsed counts
become integers. The expected counts come from contract tuple lengths and kind-f
constraints; the expected SHA is the imported constant, not a newly computed digest.

At the reviewed source, these values are 40 relations, 507 columns, 382 constraints,
131 indexes and 87 foreign keys, with SHA
6dff163cf72add13406d168d8e7389cdada4e6885e345c2534307753c5d24f4c.
The test detects disagreement between the atlas header and those imported values.
It does not independently establish that the SHA accurately hashes the contract,
that SQL installs it or that a live database has it. The witness checks a parsed
relation count and duplicate recognized headers; arbitrary malformed-header
variants are not exhaustively tested.

The second test extracts exact level-three headings containing backticked cpk_
table names. It requires the insertion order of recognized sections to equal the
contract's relation order. Duplicate recognized table names raise immediately.
Within each section it parses single-line bold-label bullets, rejecting duplicate
recognized labels. Every table must expose these eleven fields in this order:

- Durable meaning and owner;
- Identity and cardinality;
- Outgoing foreign keys;
- Inbound dependents;
- Writers and transactions;
- Readers and projections;
- Mutation, locks, retries, and idempotency;
- Lifecycle, retention, deletion, and restore;
- JSON boundary;
- Sensitive material;
- Future impact.

Values must be nonempty after trimming. Additional recognized labels fail the
ordered-field comparison. Unrecognized prose is ignored: paragraphs between a
heading and its bullets are allowed, as the activity-plan section demonstrates.
This is a deliberately narrow Markdown parser, not a complete document validator.
Formatting that does not match its expressions may be invisible to it.

The third test adds substring requirements for six table sections. Activity events
must describe run identity as foreign-key-derived from activity runs; activity runs
must distinguish their direct run_id check from prior_run_id self-FK derivation.
Cloudflare ingress, rotation deployments and generated ingress secret references
must describe their independent run provenance checks. Secret-use authorization's
writer field must mention run_id, direct checking and locale stability.

Selected actual [DDL constraints](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
and contract entries support that distinction: run_id on activity runs has a
COLLATE "C" ASCII grammar check; the event and prior-run identities have foreign
keys to that checked identity; the named independent provenance fields have their
own checks, allowing null where appropriate. The atlas test itself never reads
these SQL statements or compares those check expressions. A required phrase can
remain present while surrounding prose becomes stale or contradictory.

The fourth test parses the marked foreign-key ledger. Each recognized row yields
constraint name, local relation, ordered local columns, referenced relation and
ordered referenced columns. Comma-separated columns are trimmed and empty pieces
discarded. A matching row must have nonblank explanatory meaning, but that meaning
does not enter the returned tuple or receive semantic verification.

The resulting tuple must exactly equal the kind-f contract entries in contract
order, and duplicate tuples are forbidden. This preserves composite column order
and catches missing, extra, reordered or changed recognized relationships. A row
with malformed syntax is skipped; losing an expected row will then fail equality,
but unrelated malformed extra text need not. The parser does not compare foreign-key
validation, deferrability, update/delete actions or match modes.

The fifth test checks two representations of the dependency shape. Marked Mermaid
edges must have the exact local-table -->|constraint-name| referenced-table form.
Lines are stripped before matching. An unmatched line containing --> raises;
other lines, including the fence and flowchart directive, are ignored. Edge tuples
must match the contract's foreign keys in order. The test does not render Mermaid
or check layout, labels outside this grammar or visual legibility.

Separately, _schema_graph creates one vertex per relation and a set of referenced
tables per local table. Multiple foreign keys to the same table collapse for cycle
analysis, while their individual edges remain represented in the earlier tuple
comparison. A kind-f constraint without a referenced relation raises. This helper
assumes the contract names valid graph vertices; it is not an untrusted-schema
validation interface.

_multi_table_sccs performs a depth-first strongly connected component calculation
using discovery indices, low links, a stack and membership tracking. Traversal and
output are sorted. Only components with more than one table are returned;
self-references are collected separately. The test requires exactly two multitable
components, then compares the set of the multi-table-scc and draft-catalogue-scc
marker tuples with the computed component set. Consequently, these two marker
labels could exchange their member lists without failing this set comparison.

The actual declarations identify the workspace/graph-version/realized-projection
cycle and the draft/draft-revision cycle. Selected contract foreign keys establish
both directions, including the initially deferred draft-head reference. The
self-reference marker names activity runs, effect attempts, secret providers and
secret references in sorted order. The test does not interpret these cycles as
errors or derive an executable restore plan from them. It does not account for
application-level dependencies that are absent from foreign keys.

_parse_marker requires exactly one regex-matching declaration for a requested
marker and splits its comma-separated names. The tests exercise duplicate
multi-table-scc and self-reference declarations with the witness. The separate
outcome-aggregate marker in the real atlas is not checked by this suite. Neither
the synthetic witness nor dedicated test vectors exercise the SCC algorithm over
multiple independently specified graph shapes.

Both ledger and graph extraction use _between. A missing start delimiter raises,
but the implementation's second split accepts the remaining text if the end
delimiter is missing. Repeated delimiters are not independently rejected; the first
start and following end determine extraction. These parser limits should not be
mistaken for strict structural-boundary validation.

The sixth test fixes the future-impact marker to 1553,1554,1555,1556,1243,1244 in
that order, with a duplicate-marker witness. It requires four policy phrases about
object-free installation, exact current schema, reset-required behavior and absence
of runtime changes. Five retired schema symbols must be absent. Two phrases assign
signing-reference authorization and atomic command-intent composition to #1556;
their specified stale #1555 counterparts must be absent.

These are textual handoff guardrails. They neither retrieve GitHub issues nor prove
their current status, implementation completion or workflow transaction behavior.
The atlas's assembly/restore narrative, factoring review and most per-table owner,
retention, security and reader/writer claims still require human source review.

A concrete existing coverage gap is visible in the reviewed atlas. The
cpk_secret_use_authorizations section says no current relation references it, but
the same atlas's exact ledger and the actual contract contain
cpk_node_control_attempts_transit_authorization_workspace_fk and
cpk_node_control_attempts_workload_authorization_workspace_fk. Both bind node-control
attempts to same-workspace authorizations. The field is nonempty and the ledger is
accounted separately, so none of these six tests compares that inbound prose with
the dependency graph. This note records the inconsistency without changing the
atlas, source or tests.

The selected discovery command in the package [test runner](../../../../control-plane-kit-operations/test.sh)
uses unittest discover -s tests after installing the packages in its container.
That includes this test by its filename. The enclosing package run provisions
PostgreSQL for other tests; this module's assertions remain file/contract checks.
Reading the runner is not evidence that the runner or these tests passed here.

Read depth: full test453/all six, including every parser and witness, was read.
Atlas reads covered the header, reading/dependency/restore discussion, future map,
full graph and ledger, and selected table sections carrying the exact phrase and
inbound-reference claims. The entire 985-line atlas prose was not independently
reviewed. Contract dataclass definitions were read; static AST inspection counted
all four literal tuples and foreign keys and displayed relation order plus selected
foreign-key coordinates/flags. Named run-check entries and relevant SQL statements
were selected reads. No complete contract/DDL, store, restore or live-schema review
is claimed. Installer/verifier context is retained from their separate source pass.

Security: the note adds no credential, network, persistence or mutation surface.
Validation was limited to static source inspection, links, whitespace and comparison
with the frozen source/test baseline. The AST inspection parsed literals without
importing or executing the application module. No executable tests, application
imports, SQL, database/provider calls, schema changes or publication were performed.
Independent review and inventory/publication remain with the assigned reviewers.
