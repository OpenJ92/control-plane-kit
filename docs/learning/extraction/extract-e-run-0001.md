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

## #737 Required-Core Batch Partition

#731 was not executable immediately after #732. The closeout still had:

```text
incomplete required-core laws: 749
unmapped required-core families: 99
```

So #737 inserted the reviewable topology between #732 and #731:

```text
#737
  -> #738 pure core language
  -> #739 planning, scheduling, and saga
  -> #740 operations contracts
  -> #741 architecture and test harness
  -> #742 validation, packaging, and demos
  -> #743 interpreters and runtime substrate
    -> #731 zero-unmapped closeout
```

#743 was added during the dry run. Docker, probe, effect-materialization,
control HTTP, host publication, ownership, and retention laws are not server
products, but they are also not pure core language. They need their own
interpreter/runtime batch.

The machine-readable partition is:

```text
artifacts/extraction/required-core-batch-plan.json
```

Batch counts:

```text
#738 pure core language:                 179 laws / 20 families
#739 planning, scheduling, and saga:       74 laws / 11 families
#740 operations contracts:                275 laws / 33 families
#741 architecture and test harness:        58 laws / 10 families
#742 validation, packaging, and demos:     17 laws /  4 families
#743 interpreters and runtime substrate:  146 laws / 21 families
```

The batch-plan test proves every family from
`required-core-family-inventory.json` appears exactly once in the plan and that
the partition still totals 749 laws across 99 families.

#731 remains blocked until these batches map or explicitly supersede the
remaining laws.

## #745 Pure-Core Batch Ownership Audit

The first #738 dry run found that the #737 partition was too coarse for
execution. `pure_core_language` had 179 laws across 20 families, but not every
family really belongs to extracted core.

The audit artifact is:

```text
artifacts/extraction/pure-core-batch-audit.json
```

It keeps the original #737 batch plan immutable and records a review amendment:

```text
retain: 17 families
move:    2 families
split:   1 family
```

Important reclassifications:

```text
test_postgres_scenario_runner -> #740 operations contracts
test_block_control_fastapi    -> #743 interpreter/runtime substrate
test_contracts                -> split between #748 pure contracts and #740 operations
```

This prevents extracted core from absorbing Postgres scenario execution,
FastAPI process behavior, or derived-resource publication workflow merely
because those tests mention graph values.

The refined #738 topology is:

```text
#745 audit pure-core batch ownership
  -> #746 topology, sockets, protocol, compile
    -> #747 graph codec, validation, diff
      -> #748 configuration, environment, secrets, verification contracts
        -> #749 policy, probe intent, control-contract boundary
          -> #750 pure-core mapping batch closeout
```

## #746 Topology, Protocol, And Compile Successors

#746 mapped the retained pure topology slice from the #745 audit:

```text
test_algebra:             3 laws
test_compile:             1 law
test_connection_protocol: 3 laws
test_graph_construction:  4 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-746-topology-protocol-compile.json
```

The new successor tests live in extracted core:

```text
control-plane-kit-core/tests/test_kernel_pipeline.py
control-plane-kit-core/tests/test_protocol.py
control-plane-kit-core/tests/test_topology_graph.py
```

Curated shape:

```python
DeploymentTopology
  -> compile_topology
    -> DeploymentGraph
```

The added tests preserve:

- requirement sockets must have startup environment bindings unless they are
  runtime-control sockets;
- runtime-control sockets cannot smuggle startup environment names;
- HTTP and Postgres dependencies can compose through a split service topology;
- protocol compatibility requires both transport and application semantics;
- DNS and raw protocols retain distinct TCP/UDP meanings;
- every protocol has a closed endpoint scheme set;
- duplicate node, edge, and runtime identities fail during pure construction
  without last-write-wins replacement.

Required-core inventory after #746:

```text
incomplete required-core laws: 738
unmapped required-core families: 95
```

Batch-plan validation was corrected during #746. The #737 batch plan is an
immutable snapshot of the original required-core partition, while each successor
mapping intentionally shrinks the live required-core inventory. The test now
proves:

```text
batch plan source counts
  == exactly one partition of the original planned references

current required-core inventory
  <= original batch-plan snapshot
```

That keeps the extraction law strict without pretending the live inventory
should remain equal after mappings have been completed.

## #747 Graph Codec, Validation, And Diff Successors

#747 mapped the remaining pure graph-language slice from #738:

```text
test_graph_codec:      11 laws
test_graph_validation: 12 laws
test_graph_diff:       15 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-747-graph-codec-validation-diff.json
```

The new successor tests live in extracted core:

```text
control-plane-kit-core/tests/test_graph_codec.py
control-plane-kit-core/tests/test_graph_validation.py
control-plane-kit-core/tests/test_graph_diff.py
```

Curated shape:

```text
DeploymentGraph
  -> GraphDescriptorCodec
    -> closed durable descriptor

DeploymentGraph
  -> validate_graph
    -> ValidatedGraph[ValidationFinding]

ValidatedGraph x ValidatedGraph
  -> diff_graphs
    -> GraphDiff[StructuralChange]
```

The added tests preserve:

- graph descriptors reject unknown variants, malformed shapes, inline secret
  environment values, unknown protocol factors, and lossy unknown fields;
- secret-reference endpoints round-trip as opaque graph data without secret
  resolution;
- custom block-spec codecs preserve typed identity instead of reconstructing
  from strings;
- validation returns deterministic structured findings for runtime ownership,
  provider endpoints, edge assignment, duplicate socket, missing connection,
  verification target, and descriptor invalidity laws;
- diff output is typed structural data with separate subjects for endpoints,
  sockets, block specifications, metadata, environments, configuration
  artifacts, secret deliveries, runtime containment, unsupported transitions,
  and ambiguity;
- diff descriptors redact secret references, secret-shaped metadata, and
  environment assignments.

One dry-run correction was important: a runtime-control router switch is not a
socket-derived environment change. The successor for the socket-environment
diff law now uses a startup environment-bound service dependency whose provider
endpoint changes, while runtime-control routing remains covered as a typed edge
change.

Required-core inventory after #747:

```text
incomplete required-core laws: 700
unmapped required-core families: 92
```

## #748 Topology Split

#748 was split because the original surface grouped several different pure
languages into one large issue. The child topology is now:

```text
#754 configuration artifacts and rendering
  -> #755 environment bindings and secret deliveries
    -> #756 verification contracts and capabilities
      -> #748 parent closeout
```

The old `test_contracts` family remains deliberately split. Pure descriptor and
value laws may map through the #754/#755/#756 children, but derived-resource
mutation, live patching, publication winners, and operation-like semantics must
not be pulled into extracted core here.

## #754 Configuration Artifact And Rendering Successors

#754 mapped the pure configuration artifact/rendering slice:

```text
test_configuration_artifacts: 7 laws
test_configuration_rendering: 5 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-754-configuration-rendering.json
```

The extracted-core successor tests live in:

```text
control-plane-kit-core/tests/test_configuration_artifacts.py
```

Curated shape:

```text
ConfigurationTemplate x ConfigurationParameters
  -> strict Jinja2 rendering
    -> ConfigurationArtifact

ConfigurationArtifact
  -> descriptor
    -> digest-verified durable graph value
```

The implementation added `control_plane_kit_core.configuration_rendering` as a
pure data-to-data interpreter. It uses a sandboxed strict Jinja2 environment
with only the deterministic `json` filter exposed. It does not touch Docker,
filesystems, stores, or product-server code.

Important boundary decision: frozen tests that mentioned pinned start material
or reconcile work were mapped only to their pure core content-preservation and
graph-diff laws. Runtime effect materialization remains outside extracted core.

Validation:

```text
focused extracted-core #754 unittest: 10 tests passed
```

Required-core inventory after #754:

```text
incomplete required-core laws: 688
unmapped required-core families: 90
```

## #755 Environment Binding And Secret Delivery Successors

#755 mapped the pure environment/secret slice:

```text
test_environment_bindings: 5 laws
test_secret_delivery_topology: 4 laws
test_secrets: 9 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-755-environment-secrets.json
```

The extracted-core successor tests live in:

```text
control-plane-kit-core/tests/test_environment_secrets.py
```

Curated shape:

```text
PublicStaticEnvironmentBinding
  -> descriptor
    -> durable non-secret graph value

SocketDerivedEnvironmentBinding
  -> edge identity x endpoint literal
    -> durable non-secret graph value

SecretReference x SecretDelivery
  -> descriptor
    -> opaque graph secret intent

SecretResolver x SecretReference
  -> SecretResolution
    -> explicit runtime-only SecretValue release
```

Important boundary decision: exact `SecretReference` identity is allowed in
durable graph descriptors because it is opaque identity, not resolved secret
content. Diff descriptors are a different projection boundary: they now redact
`reference_id` and include a stable SHA-256 `reference_fingerprint` so operators
can see that an opaque reference changed without printing the exact secret path.

Runtime secret provider integration remains outside extracted core. The local
development resolver laws are retained here only as a pure boundary language:
authority, missing/denied/resolved outcomes, and redacted explicit release.

Validation:

```text
focused extracted-core #755 unittest: 17 tests passed
```

Required-core inventory after #755:

```text
incomplete required-core laws: 670
unmapped required-core families: 87
```

## #756 Verification Contract And Capability Successors

#756 mapped the pure verification/capability slice:

```text
test_verification_contract: 11 laws
test_verification_dispatch: 3 laws
test_capabilities: 5 laws
test_capability_compile: 1 law
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-756-verification-capabilities.json
```

The extracted-core successor tests live in:

```text
control-plane-kit-core/tests/test_verification_capabilities.py
```

Curated shape:

```text
VerificationContract
  = tuple[VerificationCheck, ...]

VerificationCheck
  -> descriptor
    -> closed protocol-scoped declaration

VerificationCheck
  -> expected_protocols
    -> frozenset[Protocol]

VerificationCheck
  -> verification_capability
    -> VerificationCapability

CapabilityName
  -> capability_named
    -> Capability
      -> route-set-backed descriptor | capability-only descriptor
```

Boundary decision: #756 maps only the pure declaration and result language.
Probe execution, adapter dispatch, command services, stores, API, CLI, MCP, and
Docker probe behavior remain outside extracted core. Frozen dispatch tests were
therefore mapped to the pure `VerificationCompleted` and
`VerificationUnsupported` result values, not to an interpreter registry inside
core.

Validation:

```text
focused extracted-core #756 unittest: 17 tests passed
```

Required-core inventory after #756:

```text
incomplete required-core laws: 650
unmapped required-core families: 83
```

## #760 Contract Boundary Classification

#760 refined the last mixed family in the #738 pure-core batch:

```text
source family: tests.test_contracts
remaining laws: 41
pure value successor laws: 18 -> #764
operations-owned laws: 23 -> #740
```

Artifact:

```text
artifacts/extraction/contract-boundary-classification.json
```

The important boundary is:

```text
ControlVariableSpec / ControlValueKind / redacted descriptors
  -> pure control-contract value language
  -> #764

EnvironmentContract / RuntimeContract mutable holder behavior
  -> patching, publication, idempotency, derived resources, cleanup
  -> #740
```

Curated decision:

```text
pure successor candidates
  = descriptor shape
  + required/optional validation
  + protocol shape validation
  + secret redaction
  + explicit runtime state construction
  + no process environment read

operations-owned candidates
  = apply_patch
  + prepare/apply mutation
  + stale version and replay identity
  + derived resource build/dispose/staleness
  + cleanup uncertainty
  + concurrent publication winner
```

#760 created #764 because policy and probe-intent work should not absorb the
control-contract value slice. The topology under #749 is now:

```text
#760 classify remaining test_contracts boundary laws
  -> #764 map pure control-contract value laws
  -> #763 map pure policy decision laws
  -> #761 map pure probe-intent and observation value laws
  -> #762 reclassify control-route process laws and close #749
```

Validation:

```text
focused #760 classification unittest: 4 tests passed
full Docker/Postgres ./test.sh suite: 1179 tests passed
```

## #764 Pure Control-Contract Value Laws

#764 mapped the pure value slice of the `tests.test_contracts` family into the
extracted core package. The new core object is:

```text
ControlVariableSpec
  -> validate(value)
  -> descriptor(value?, include_value?, unsafe?, redact_value?)

ControlContract
  -> load(explicit mapping)
  -> ControlContractSnapshot
    -> descriptor()          # redacted
    -> unsafe_descriptor()   # explicit unsafe mode; secrets still redacted
    -> prepare_patch(...)    # validates candidate only; does not publish
```

This is intentionally not the frozen mutable `EnvironmentContract`/
`RuntimeContract` holder. It is the pure declaration and validated snapshot
language that later operations code can interpret.

Mapped laws:

```text
test_contracts pure successor laws: 18 -> extract-e-764.control-contracts.unittest
test_contracts operations laws: 23 remain visible -> #740
```

Important implementation decision:

```python
def load(self, values: Mapping[str, object]) -> "ControlContractSnapshot":
    """Validate explicit supplied values without reading process state."""

def load_from_process(self) -> "ControlContractSnapshot":
    raise TypeError("control-plane-kit-core does not read process environment")
```

Variable descriptors preserve the old opt-in law for ordinary values, while
contract snapshot descriptors redact by default:

```python
message.descriptor("hello", include_value=True)["value"] == "hello"

snapshot.descriptor()["variables"]["storage_base_url"]["value"]
  == {"present": True, "redacted": True}
```

Artifact:

```text
artifacts/extraction/successor-proofs/extract-e-764-control-contracts.json
```

Validation:

```text
focused #764 red evidence: missing control_plane_kit_core.control_contracts
focused #764 extracted-core unittest: 9 tests passed
current-tree extraction parity slice: 20 tests passed
control-plane-kit-core/test.sh: 243 tests passed; compileall passed; import ok
```

Required-core inventory after #764:

```text
completed required-core laws: 148
incomplete required-core laws: 632
unmapped required-core families: 83
```

## #763 Pure Policy Decision Laws

#763 mapped the complete frozen `tests.test_policies` family into extracted
core. The object is intentionally small:

```text
typed facts
  = scopes x ActivityPlan x activity operation x WorkspaceLifecycle

Policy
  : typed facts -> PolicyDecision | ApprovalRequirement | LifecycleRetention
```

The new module is:

```text
control_plane_kit_core.policies
```

and its important values are:

```text
PolicyScope
PolicyDecision
HubAccessPolicy
InstanceAccessPolicy
ApprovalPolicy
ApprovalRequirement
DestructiveActivityPolicy
LifecycleRetention
retention_for
```

The root package does not re-export the new `policies.ApprovalPolicy` yet,
because the operations parity layer already exports an `ApprovalPolicy` enum at
the root. Keeping the new policy language under `control_plane_kit_core.policies`
avoids a misleading name collision while preserving the pure language.

The core law is:

```python
def _require_scope(
    actor_scopes: Iterable[PolicyScope],
    required: PolicyScope,
) -> PolicyDecision:
    scopes = set(actor_scopes)
    if not all(isinstance(scope, PolicyScope) for scope in scopes):
        raise TypeError("actor scopes must be PolicyScope values")
    if required in scopes:
        return PolicyDecision.allow(f"scope {required.value!r} is present")
    return PolicyDecision.deny(
        f"scope {required.value!r} is missing",
        required_scope=required,
    )
```

That snippet matters because the successor does not preserve open string
authority at the core boundary. Actor scopes are closed `PolicyScope` values.

Approval policy remains an interpreter over canonical plan data:

```python
def requirement_for(self, plan: ActivityPlan) -> ApprovalRequirement:
    max_risk = max(
        (activity.risk for activity in plan.activities),
        key=_RISK_ORDER.__getitem__,
        default=RiskLevel.INFORMATIONAL,
    )
    destructive = any(
        activity.impact is ActivityImpact.DESTRUCTIVE
        for activity in plan.activities
    )
    return ApprovalRequirement(
        required_scope=(
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE
            if destructive
            else PolicyScope.PLAN_APPROVE
        ),
        max_risk=max_risk,
        destructive=destructive,
    )
```

Mapped laws:

```text
test_policies laws: 6 -> extract-e-763.policy-decisions.unittest
```

Artifact:

```text
artifacts/extraction/successor-proofs/extract-e-763-policy-decisions.json
```

Validation so far:

```text
focused #763 red evidence: missing control_plane_kit_core.policies
focused #763 extracted-core unittest: 7 tests passed
focused #763 module inventory slice: 10 tests passed
current-tree extraction parity slice: 17 tests passed
control-plane-kit-core/test.sh: 250 tests passed; compileall passed; import ok
full Docker/Postgres ./test.sh suite: 1181 tests passed
```

Required-core inventory after artifact update:

```text
completed required-core laws: 154
incomplete required-core laws: 626
unmapped required-core families: 82
```

## #761 Pure Probe-Intent And Observation Value Laws

#761 mapped the frozen `tests.test_probe_intents` family into extracted core as
a pure observation-intent language. The object is:

```text
ProbeSubject x RuntimeEndpointObservation x ProbePolicy
  -> ProcessProbeIntent
   | TransportProbeIntent
   | ApplicationHealthProbeIntent
   | ReadinessProbeIntent

ProbeKind x ProbeOutcome x EndpointContext?
  -> ProbeObservation
```

The module is:

```text
control_plane_kit_core.probe_intents
```

It is deliberately separate from `control_plane_kit_core.verification`.
Verification contracts describe package-owned semantic checks. Probe intents
describe runtime observation layers: process, transport, application health,
and readiness. They are adjacent languages, not the same object.

Important law:

```python
def probe_outcome_is_valid(kind: ProbeKind, outcome: ProbeOutcome) -> bool:
    if not isinstance(kind, ProbeKind) or not isinstance(outcome, ProbeOutcome):
        return False
    return outcome in _OUTCOMES_BY_KIND[kind]
```

That table keeps observation layers honest. A healthy application does not imply
readiness; reachable transport does not imply application health; process start
does not imply either.

Endpoint material remains pure graph/runtime evidence:

```python
RuntimeEndpointObservation(
    "api",
    "internal",
    "graph-a",
    Protocol.HTTP,
    EndpointContext.PUBLIC,
    SecretEndpointMaterial("secret://workspace/public-api"),
).descriptor()["address"]
```

returns only:

```python
{"kind": "secret-reference", "reference_id": "secret://workspace/public-api"}
```

Mapped laws:

```text
test_probe_intents laws: 13 -> extract-e-761.probe-intents.unittest
```

Artifact:

```text
artifacts/extraction/successor-proofs/extract-e-761-probe-intents.json
```

Validation so far:

```text
focused #761 red evidence: missing control_plane_kit_core.probe_intents
focused #761 extracted-core unittest and module inventory slice: 16 tests passed
current-tree extraction parity slice: 18 tests passed
control-plane-kit-core/test.sh: 263 tests passed; compileall passed; import ok
full Docker/Postgres ./test.sh suite: 1182 tests passed
```

Required-core inventory after artifact update:

```text
completed required-core laws: 167
incomplete required-core laws: 613
unmapped required-core families: 81
```

## #762 Control-Route Boundary Closeout

#762 finished the policy/probe/control-contract boundary parent by separating
the last control-route-adjacent families.

Pure successor:

```text
tests.test_control_routes
  -> control_plane_kit_core.control_routes
```

This maps the closed route descriptor language:

```text
ControlRouteSetName x ControlRouteMethod x ControlRouteScope x path
  -> ControlRoute
  -> ControlRouteSet
  -> JSON-friendly descriptor
```

Important snippet:

```python
def route_set_named(name: ControlRouteSetName | str) -> ControlRouteSet:
    allowed = ", ".join(route_set.name.value for route_set in CONTROL_ROUTE_SETS)
    try:
        route_set_name = ControlRouteSetName(name)
    except ValueError as exc:
        raise KeyError(
            f"unknown control route set {name!r}; known route sets: {allowed}"
        ) from exc
    for route_set in CONTROL_ROUTE_SETS:
        if route_set.name == route_set_name:
            return route_set
    raise KeyError(f"unknown control route set {name!r}; known route sets: {allowed}")
```

That is pure descriptor logic. It names the route protocol; it does not build a
FastAPI app, parse requests, authenticate tokens, mutate runtime state, or
dispatch effects.

Downstream classifications:

```text
test_block_control_fastapi -> #740 / cpk-server or package-owned process boundary
test_block_control_state -> #740 / operations and process composition boundary
test_capability_interpreter_registry -> #743 / interpreter and effect dispatch boundary
```

These classifications are recorded in:

```text
artifacts/extraction/control-route-boundary-classification.json
```

Mapped laws:

```text
test_control_routes laws: 6 -> extract-e-762-control-routes.unittest
```

Artifact:

```text
artifacts/extraction/successor-proofs/extract-e-762-control-routes.json
```

Validation so far:

```text
focused #762 extracted-core route descriptor slice: 9 tests passed
focused #762 artifact and parity guard slice: 21 tests passed
validate-parity.sh foundation: valid=true, findings=0
control-plane-kit-core/test.sh: 269 tests passed; compileall passed; import ok
full Docker/Postgres ./test.sh suite: 1185 tests passed
```

Required-core inventory after artifact update:

```text
completed required-core laws: 173
incomplete required-core laws: 607
unmapped required-core families: 80
```

## #750 Pure-Core Mapping Batch Closeout

#750 closed the #738 pure-core batch by checking the original #737 batch plan
against the #745 audit, the live parity manifest, and the current required-core
inventory.

The new closeout artifact is:

```text
artifacts/extraction/pure-core-batch-closeout.json
```

Its governing shape is:

```text
RequiredCoreBatchPlan
  x PureCoreBatchAudit
  x CurrentRequiredCoreInventory
    -> PureCoreBatchCloseout
```

The important law is not "no required-core laws remain." #738 was only one
batch. The law is:

```text
retained pure-core families
  -> mapped successor evidence

moved families
  -> visible on their active downstream issue

split families
  -> pure slice mapped, non-core slice still visible downstream
```

Closeout counts:

```text
source families:                         20
source laws:                             179
retained source laws:                    115
moved source laws:                        23
split source laws:                        41
retained families still live:              0
remaining moved-or-split live laws:        46
```

The remaining live laws are intentional:

```text
test_postgres_scenario_runner -> #740 operations contracts
test_block_control_fastapi    -> #743 interpreter/runtime substrate
test_contracts                -> #740 operations slice after #748/#764 pure mapping
```

This closes #738 without pretending operations, FastAPI process behavior, or
interpreter/runtime substrate laws were migrated into extracted core.

Validation:

```text
focused #750 batch/parity guard slice: 21 tests passed
validate-parity.sh foundation: valid=true, findings=0
```
