# EXTRACT.E Core Release-Candidate Parity - Run 0001

## Scope

EXTRACT.E certifies the extracted core release candidate and stops before
server-repository implementation.

It does not package `cpk-server` inside core.

```text
control-plane-kit-core
  owns pinned wheel evidence
  owns parity and architecture evidence
  owns public contract language
  owns cpk-server handoff contracts

control-plane-kit-servers/cpk-server
  owns FastAPI/MCP process composition
  owns Dockerfile and OCI image
  owns product descriptor
  owns live process publication evidence
```

## E.0 Result

#725 refreshes EXTRACT.E so it inherits the EXTRACT.D boundary instead of the
older plan where core produced a CPI image or self descriptor.

The corrected topology is:

```text
#642 -> #725
#725 -> #643
#725 -> #644
#643 + #644 -> #645 -> #646 -> #647 -> #648
```

## Child Classification

| Issue | Classification | Why |
| --- | --- | --- |
| #725 | Topology refresh | Confirms EXTRACT.E output is core wheel plus manifests, parity evidence, review evidence, and cpk-server handoff readiness. |
| #643 | Core law reconciliation | Required-core laws must have passing successors or reviewed supersession. Deferred product laws stay visible and deferred. |
| #644 | Core public-interface parity | Core-owned examples must traverse extracted public imports and contract language without claiming live cpk-server process ownership. |
| #645 | Architecture/package/public language review | Proves package DAG, module inventory, base import, public exports, and descriptor boundaries. |
| #646 | Security/data/supply-chain/test-integrity review | Reviews core contract security, transaction laws, wheel dependencies, secret exclusion, and changed tests. |
| #647 | Core wheel and evidence manifest | Publishes or locally proves pinned core wheel evidence and manifests. It must not create cpk-server image or descriptor artifacts. |
| #648 | Mandatory stop | Reports core release-candidate readiness and stops before server-product migration. |

## Stale Assumptions Not Migrated

- "Core publishes a CPI image" is stale.
- "Core publishes a cpk-server self descriptor" is stale.
- "Core runs live FastAPI/MCP cpk-server process demos" is stale.
- "Core owns concrete Postgres/Docker runtime interpreters" is stale for the
  extracted package.
- "Hello proves the cpk-server wrapper" is stale. Hello is the first ordinary
  external product proof; `cpk-server` needs its own wrapper topology in
  EXTRACT.F.

## Law Cards

### E.0 Topology Law

- Reference identity: `EXTRACT.E.0.topology-boundary`
- Evidence source: #642, #725, #599, #643-#648, #600, #676, and
  `SERVER_PRODUCT_ROLLOUT.md`.
- Observable law: EXTRACT.E certifies core release-candidate artifacts and
  handoff readiness without producing server process artifacts.
- Expected result: issue topology and rollout docs name #725 before #643/#644
  and route cpk-server image/descriptor/live evidence to the server milestone.
- Negative cases: a core-owned CPI image, core-owned self descriptor, hosted
  FastAPI/MCP process in core, or deferred server/product law counted as
  migrated.
- Future owner: core for wheel/evidence; `control-plane-kit-servers/cpk-server`
  for process/image/descriptor/live evidence.

## Objects And Transformations

```text
FrozenLawInventory
  -> ParityManifest
    -> RequiredCoreLawReconciliation

CorePublicImports
  -> PublicInterfaceExamples
    -> CoreReleaseCandidateEvidence

CpkServerHandoffContracts
  -> ServerRepositoryImplementationObligations
```

## Handoff To #643

#643 should start from the parity manifest and required-core ownership ledger.
It must prove required-core zero-unmapped evidence without treating server
product laws as migrated.

It should preserve this distinction:

```text
required core law
  -> passing extracted-core successor

deferred server/product law
  -> visible deferred ledger entry
```

Do not import frozen implementation code, and do not preserve obsolete module
paths to make parity look easier.

## #643 Dry Run And Child Topology

The #643 dry run showed that required-core reconciliation is too broad for one
PR. The current parity artifacts start with:

```text
manifest entries:       1107
required entries:        880
deferred entries:        227
core-owned required:     780
core-owned completed:     24
core-owned unmapped:     756
```

The child topology is:

```text
#728 -> #730 -> #729 -> #732 -> #731
```

| Issue | Role |
| --- | --- |
| #728 | Define required-core closeout report and fail-closed validator. |
| #730 | Inventory unmapped required-core laws by frozen module/family. |
| #729 | Map high-confidence extracted-core successor suites by family. |
| #732 | Classify reviewed supersessions for obsolete core structure. |
| #731 | Produce zero-unmapped required-core closeout. |

The governing law is:

```text
RequiredCoreCloseout
  = ParityManifest
  x SuccessorEvidence
  x CoreOwnerSlice
  -> RequiredCoreReport
```

A green aggregate suite is not itself a parity mapping. It becomes evidence
only when a manifest successor explicitly points at it. Deferred product/server
entries stay visible in the report but do not count as migrated core behavior.

## #728 Required-Core Closeout Report

#728 adds the first interpreter for the EXTRACT.E core slice:

```python
def validate_required_core_closeout(
    manifest: dict[str, object],
    ownership: dict[str, object],
    demos: dict[str, object],
    evidence_index: dict[str, object],
) -> dict[str, object]:
    ...
```

The report is deliberately narrower than `MIGRATION_COMPLETE`. It requires
core-owned required entries to be complete, while keeping Hello, system, and
deferred product/server work visible for later milestones.

Important result shape:

```python
{
    "schema": "cpk.required-core-parity-closeout",
    "valid": False,
    "required_core_complete": False,
    "counts": {
        "required_core": 780,
        "required_non_core": 100,
        "deferred": 227,
        "completed_required_core": 24,
        "incomplete_required_core": 756,
    },
    "deferred_entries": [...],
    "incomplete_required_core_entries": [...],
    "findings": [...],
}
```

This is intentionally a failing report on the current manifest. #728 gives
#730 a precise object to inventory rather than letting #643 drift into a fake
suite-count proof.

## #730 Required-Core Family Inventory

#730 adds the second interpreter for the EXTRACT.E core slice:

```python
def inventory_unmapped_required_core_families(
    closeout_report: dict[str, object],
) -> dict[str, object]:
    ...
```

The transformation is:

```text
RequiredCoreCloseoutReport
  -> RequiredCoreFamilyInventory
```

This is only a review view. It does not claim that a frozen law has migrated,
and it does not add successor evidence. It groups the still-incomplete
required-core law identities so #729 can map high-confidence successor suites
family by family.

The generated artifact is:

```text
artifacts/extraction/required-core-family-inventory.json
```

Current real-artifact counts:

```text
unmapped required-core entries: 756
families:                       100
```

Largest families:

```text
test_contracts:                41
test_execution_coordinator:    28
test_instance_read_service:    21
test_run_lifecycle:            21
test_docker_effects:           17
test_execution_values:         16
test_probe_execution:          16
test_architecture_analysis:    15
test_graph_diff:               15
test_postgres_scenario_runner: 15
```

Important shape:

```python
{
    "schema": "cpk.required-core-family-inventory",
    "counts": {"entries": 756, "families": 100},
    "families": [
        {
            "family": "test_contracts",
            "count": 41,
            "entries": [
                {
                    "kind": "test",
                    "reference": "...",
                    "law": "behavior.secret-descriptor-never-exposes-raw-value",
                    "owner_kind": "core",
                    "owner": "control-plane-kit-core",
                },
            ],
        },
    ],
}
```

The closeout entry identity now carries `law` everywhere, including deferred
entries. That keeps the report language closed around the actual behavioral
law, not just the frozen test path. Duplicate law identities fail closed before
the family report is produced.

## #729 ActivityPlan Successor Mapping Slice

#729 begins with one deliberately small high-confidence family:

```text
test_activity_plan
```

This family was chosen because the extracted core already owns
`control-plane-kit-core/tests/test_activity_plan.py`, and the remaining frozen
laws map directly to the same closed `ActivityPlan` algebra rather than to a
broader system scenario.

Seven frozen laws were mapped:

```text
behavior.destructive-activity-requires-high-or-critical-risk
behavior.empty-plan-and-missing-lookup-are-explicit
behavior.every-closed-operation-accepts-only-its-typed-target
behavior.independent-activities-use-id-as-deterministic-tie-breaker
behavior.missing-duplicate-self-and-cycle-dependencies-fail-structurally
behavior.review-change-cannot-be-downgraded-to-low-risk
behavior.runtime-lifecycle-operations-use-runtime-targets
```

The proof artifact is:

```text
artifacts/extraction/successor-proofs/extract-e-729-activity-plan-family.json
```

It is indexed as:

```text
extract-e-729.activity-plan-family.unittest
sha256:2c7b9252548e9b8232c1121d4d9a0fa65f0e8e752357150d15c5a5ffd34bb18f
```

The target test command is:

```text
docker run --rm \
  -v /Users/jacobvartuli/Software/self/control-plane-kit/control-plane-kit-core:/pkg:ro \
  -w /pkg \
  -e PYTHONPATH=/pkg/src \
  -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.14-slim \
  python -m unittest tests.test_activity_plan
```

Important successor snippet:

```python
def test_every_closed_operation_accepts_only_its_typed_target(self) -> None:
    valid_operations = (
        (StartNode(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
        ...
        (
            DestroyDataResource(DataResourceTarget("postgres", "volume")),
            RiskLevel.CRITICAL,
            ActivityImpact.DESTRUCTIVE,
        ),
    )
```

That snippet matters because the successor test did not weaken the safety law
to make operation coverage pass. Data destruction remains critical and
destructive; review changes remain high risk.

The #729 artifact guard is:

```python
def test_activity_plan_family_is_fully_mapped_to_passing_successor_evidence(
    self,
) -> None:
    inventory = inventory_unmapped_required_core_families(self.closeout())
    families = {family["family"]: family for family in inventory["families"]}
    self.assertIsNone(families.get("test_activity_plan"), ...)
```

This was red before the manifest update with:

```text
test_activity_plan still has 7 unmapped laws
```

Current counts after the slice:

```text
completed required-core:   31
incomplete required-core: 749
unmapped families:         99
```

No deferred server/product laws moved. No aggregate suite pass was treated as
evidence without explicit manifest successors.

## #732 Reviewed Supersession Classification

#732 tightened the supersession language before attempting to remove any frozen
law from the required-core inventory.

The manifest supersession shape is now a complete reviewed structural record:

```python
SUPERSESSION_FIELDS = {
    "rationale",
    "review",
    "obsolete_assumption",
    "replacement",
    "negative_case_disposition",
}
```

That shape matters because supersession is not weaker evidence. It is a
different proof that says:

```text
this frozen assertion was about obsolete structure
  -> this stronger successor or handoff replaces it
    -> this is where the negative case remains protected
```

The core closeout validator now rejects non-core supersession during this
phase:

```python
if entry["supersession"] is not None and entry["owner_kind"] != "core":
    findings.append(
        _finding(
            "non_core_supersession_in_core_closeout",
            entry["kind"],
            entry["reference"],
            "core closeout cannot supersede non-core behavior",
        )
    )
```

The dry run reviewed the tempting candidates:

```text
test_architecture_analysis
test_architecture_dependencies
validation
```

None were superseded. The reason is important: those families contain behavior
we still want, even where old package names or local structure are stale.
Architecture-analysis tests encode AST-policy language. Dependency tests encode
ownership laws. Validation tests remain release-candidate obligations. They
need successor evidence or a later explicit harness extraction, not deletion by
supersession.

The review artifact is:

```text
artifacts/extraction/supersession-reviews/extract-e-732-reviewed-supersession-classification.json
```

Current supersession counts after #732:

```text
reviewed supersessions: 0
required-core completed: 31
required-core incomplete: 749
```

#731 must therefore treat every remaining required-core law as unmapped unless
it receives explicit successor evidence in a later mapping pass. It should not
expect #732 to reduce the count.
