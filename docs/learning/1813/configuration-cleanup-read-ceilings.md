# D2 private cleanup original-read ceilings (#1939)

Source stage, released after independent baseline/causal-red review. Base: accepted B merge
`a15e51677ab9e901cb031ae1dffa2403fc7b7ce7`, tree
`f887663727525a6ef3a8754fdeca7b5b8ae387ca`. B's corrected hosted Core (943 tests),
Operations (2387 tests) and pinned-family backend (nine stages) passed. That
backend proves its selected family, not adoption of B or provider behavior.

## Governing laws and dry run

The approved prerequisite enforces smaller privately issued widths at existing
exact original readers. It does not enable cleanup or establish whole-command
capacity. Existing `bounded_rows` already performs a length probe and guarded
fetch; the missing behavior is issuing and propagating exact cleanup-original
ceilings through independently constructed readers.

- Isomorphic representation laws: PostgreSQL JSON text, not canonical JSON,
  governs transport (`test_postgres_configuration_material_boundary` and
  `test_receiver_authoring_context_bounds`). Known intent bytea is distinct.
- Isomorphic accounting laws: actual receiver/origin reads share the caller
  ledger; overhead, empty reads, failed reservations and prior work remain
  charged (`test_postgres_configuration_receiver_accounting`,
  `test_configuration_preparation_capacity`,
  `test_postgres_configuration_cleanup_evidence`).
- Strengthened issuance laws: exact owner-issued value, same UoW/connection,
  accounting and execution context, transaction, lexical lifetime. Equal copies
  cannot confer authority (`test_postgres_configuration_completion` and
  `test_receiver_execution_scope_queries` provide governing ownership cases).
- New ceiling laws: matching reads enforce measured expression widths before
  value transport, independently of reader caches; nonmatching reads retain
  defaults. One charged transaction check per matching point entrance. Metadata
  belongs in the authored graph ceiling; nullable widths do not erase semantics.
- Preserved security gates: approved cleanup admission and context translation
  still refuse (`test_execution_admission`, `test_configuration_instance_contract`).
  Existing mutable reciprocity remains fresh; ceilings do not solve C's separate
  mutable-cache obligation (`test_postgres_configuration_evidence`).

The private target interface is `_CleanupOriginalReadCeilingsOwner(uow)` with
`capture(guard, prefix, approved_plan, intent_identity=..., prospective_intent=...)`
and lexical `bind(issued)`. No caller-supplied width factory. The bounded source
ceiling remains the two private modules plus existing execution/receiver,
history, graph/projection and intent read entrances. No schema, capacity,
ordinary-default or `bounded_rows` contract change is authorized.

## Initial targets and fixture ownership

One `ReceiverCanonicalAcceptanceFixture` setup owns the database. A complete
receiver companion (`docker` / `api` / receiver `a` repeated 32 times) is authored
and accepted through actual owners. Its existing recorded native/health premises
are explicit and confer no provider proof. A separately registered one-artifact
`cleanup-target` obtains actual simulated-adapter D1 completion through the
coordinator, then an actual accepted departure. The original complete companion
survives on both cleanup pins. The cleanup plan and destructive approval are
real; only the request/run prefix is explicitly recorded history because public
cleanup execution remains unsupported. No cleanup intent or attempt exists in
the prospective capture case.

The baseline must prove nonempty exact persisted binding sets, original receiver
acceptance, runtime/authority, runtime-wide scope rows/digest, one selected D1
source, approved-plan/prefix correspondence, SQL representation and unchanged
durable truth using existing owners before missing-issuer red earns credit.

The first three new-law targets cover independently constructed original
readers, metadata growth before full transport, and equal-copy refusal. Dynamic
capability assertions occur in test methods after the same fixture proof, never
as import or setup failures. Dedicated retained-intent propagation, remaining
portability/transaction/context, fixed-expression/null, probe/fetch race,
distinct-pin and exact-boundary laws remain required before source-green; this
initial target slice does not claim them complete.

## First target run: fixture prerequisite failure

At target commit `323d635a9eecd540db5d8da55d4fcc66d05ae116`, the owning focused
gate ran four tests in 95.568 seconds and exited one with four assertion
failures. The existing-owner control failed, so **none earns causal-red credit**.
Each method stopped at the same existing supported-plan assertion, before the
target's missing-issuer check. Log SHA256:
`bf663671fea0a25348b93e230f0af6a8758e23a7d4caedbab2b2ef74b7b153c1`.
The gate's exact container and network were removed; this was not an apparatus
failure.

The receiver companion's initial acceptance completed. Adding the isolated
configuration target then produces an `UpdateDeployment`. Existing
`runtime_management_execution_is_unsupported` supports nonempty managed plans
only for `InitialDeployment` or `TeardownDeployment`; it therefore rejects this
update. A later selective departure preserving the receiver would cross the
same boundary. This invalidates the proposed fixture's supported-execution
assumption. Removing the assertion, substituting recorded acceptance, or enabling
managed updates would not be a routine #1939 fixture correction. The baseline
failure is referred to the coordinator/reviewer for prerequisite or explicit
read-premise disposition. Production source and reruns remain held.

North subsequently released Kepler's fixture-only reorder: actual ordinary
initial deployment, selected D1 and acceptance; actual completely empty teardown
and acceptance; then existing canonical receiver initial deployment and
acceptance. The registered Docker authority record must remain identical across
all stages. This supersedes the receiver-before-source ordering above without
changing admission or creating acceptance records directly. The ordinary initial
compiler shape is exactly StartRuntime, StartNode and WaitForHealthy; only the
selected configuration StartNode receives the correlated D1 completion. Runtime
and health results are explicitly simulated and their exact call identities are
asserted. Final historical cleanup inspection still uses the actual owner. This
sequence supplies reader premises, not managed-update or provider evidence.
Exact fixture review and a new focused baseline remain required before credit.

The reordered target checkpoint `7aa03d8c` ran four tests in 36.997 seconds and
failed the same exact artifact assertion in each: the normalized descriptor's
first artifact was `limits`, while this fixture requires `settings`. Ordinary
initial planning, coordinator execution and correlated D1 admission had passed;
departure and receiver deployment were not reached. This is another fixture
failure, with zero causal-red credit. Log SHA256:
`508bbc7af34983867d9ea06fdac92c76ed768750a9b3d139c72b1e9c1f32d2cb`.
Select the artifact by its stable `artifact_id`, assert exactly one selection
before registration, and retain the existing exact ref/query assertions. No
production or policy correction is involved.

## Existing-owner baseline and causal red

At `58f7bc99ae73819799985a1794b0ee897b2b7c4e` (tree
`e2c9ab0472e9403b80f91b6226ee94c61e224746`), the same owning focused gate ran four
tests in 149.793 seconds. The existing-owner baseline passed. All three new-law
methods passed their complete fixture premises and failed exactly at the
in-method missing private issuer assertion, with no errors. This is causal-red
evidence, not source implementation or package acceptance. Log SHA256:
`6afe790dba61f3540ae7a1b24d1eac93ad4c08cff6c7c73531c518bc42c8464d`.

The gate exited one as expected and removed its exact test container/network.
Its post-test compile/import phases did not run after expected red; no such
credit is claimed. The successful control preserves both public cleanup
refusals, checks real PostgreSQL representation, proves the complete receiver
bindings and actual source/approval/history premises, and leaves durable truth
unchanged. Independent evidence review and coordinator source release remain
required before implementing the private owner. Remaining target laws above
must still be completed before source-green.

Security: no runtime, network, authentication, secret transfer, durable production
writer or activation change. The negative metadata mutation is confined to a
rolled-back test transaction. The complete cleanup ledger and provider results
remain C/I177/E4 obligations. No executable result is claimed at target authoring.

## Source draft and completed target shape

North released source after Kepler accepted the baseline and causal-red evidence.
Two private modules now hold the frozen issued value and its PostgreSQL owner.
The owner rechecks the locked request, run prefix, complete original receiver
material, destructive approval and prospective Core intent before measuring the
fixed columns. A single-use lexical binding passes named widths into the five
existing read entrances. Matching applications check the exact issuer, UoW,
connection, accounting, execution context and transaction; nonmatching rows keep
their ordinary limits. No new public export, schema, global limit or cleanup
activation is introduced.

The target matrix now covers independent readers, coincident/distinct pins,
retained intent bytea, metadata and each original's growth, probe/fetch growth,
same-width semantic corruption, named-column caps, defensive SQL NULL handling,
copy/unissued/foreign-owner refusal, accounting/task/thread/connection/transaction
boundaries, scope exit and premature commit, exact/one-byte-over metadata, empty
reads, prelude retention and failed reservations. Current captured columns are
NOT NULL: the isolated NULL projection proves defensive transport semantics,
not valid issuance. The retained-intent test explicitly creates recorded history
separately; the prospective capture control still has no intent or attempt.

Early review fixed an accidental change to shared receiver bytea caps by restoring
the existing `_columns` implementation exactly. A real ordinary receiver-history
test requires an intent larger than 2048 bytes. Review also required a fresh
locked request comparison and exposed `bind(None)` before first issuance; exact
issued type/owner checks and a pre-capture refusal assertion close that gap.

Accounting remains cumulative. The fixed width capture plus its initial txid
reservation is 2300 bytes for coincident pins, 3516 for distinct pins; these are
not total capture costs. Additional proof includes the fresh request lock/read,
run-prefix recheck, receiver originals and persisted scope verification, and
approval request/decision reads, all charged to the same ledger. Each matching
point application adds one txid query. Coincident receiver pins therefore have
five application checks but only three original fetches in a fresh receiver
storage; the existing lifecycle check is an additional sixth txid query.

Source targets have not yet executed at this checkpoint. Focused owning evidence,
independent source review and hosted full acceptance remain required. The
inventory includes the new modules, changed import companions, and the previously
unlisted touched PostgreSQL activity-history owner. Both cleanup refusal gates
remain asserted; whole-C capacity and mutable proof freshness remain #1936 work.

## Focused source green and independent review

At `a7e9bd39f834dc1b1ed883725aa5292183a24d01` (tree
`2c314fdf7cd2c4b741aa9898cace12f98a9cb6dc`), the owning command was:

```sh
CPK_OPERATIONS_TEST_NETWORK_NAME=cpk-1939-target-test \
CPK_OPERATIONS_TEST_POSTGRES_CONTAINER=cpk-1939-target-postgres \
./control-plane-kit-operations/test.sh \
  -k PostgresConfigurationCleanupReadCeilingsTests \
  -k PostgresEffectAttemptIntentStoreContractTests \
  -k ReceiverExecutionScopeTransportTests
```

The gate exited zero: 22 tests passed in 409.367 seconds (13 ceiling laws,
five intent-store contract tests, four existing receiver transport tests).
The normal compile and clean-import phases also passed. Its exact PostgreSQL
container and Docker network were absent afterward. Log SHA256:
`ec8a0004cde504e507156d25a870ae8537c33a92c61b464e9183dbd12d77172d`.
This is focused Operations evidence, not full package or composed acceptance.

Kepler's independent bounded source review passed at that same coordinate.
All previously missing downstream laws executed successfully, including the
separately recorded intent and ordinary receiver-history paths. No source changes
occurred during validation. Hosted package and required backend acceptance remain
next; no duplicate local full suite is planned.

The normal capture statement count reconciles to 26 with coincident pins or 32
with distinct pins: initial txid 1, fresh locked request 3, locked run recheck 3,
original/scope proof 12/16, approvals 4, fixed width capture 3/5. These counts are
source-traced. Each uncached, nonempty matching read adds txid, probe and guarded
fetch; a cached application still checks txid, and an empty read stops after its
probe. Existing bounded transport already remeasures every actual fetch. The new
contract enforces an earlier measurement on later independent readers so future
work can be forecast against it; it does not replace measured per-fetch charging.
