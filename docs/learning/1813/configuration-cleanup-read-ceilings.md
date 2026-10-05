# D2 private cleanup original-read ceilings (#1939)

Target stage only. Base: accepted B merge
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

Security: no runtime, network, authentication, secret transfer, durable production
writer or activation change. The negative metadata mutation is confined to a
rolled-back test transaction. The complete cleanup ledger and provider results
remain C/I177/E4 obligations. No executable result is claimed at target authoring.
