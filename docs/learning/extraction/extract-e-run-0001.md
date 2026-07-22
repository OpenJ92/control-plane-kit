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

## #771 Planning/Saga Batch Ownership Audit

#771 starts the #739 batch with the same shape as #745: classify before
mapping. The source batch is:

```text
artifacts/extraction/required-core-batch-plan.json
batch: planning_saga
```

The audit artifact is:

```text
artifacts/extraction/planning-saga-batch-audit.json
```

The full #739 topology is:

```text
#771 audit planning/saga batch ownership
  -> #772 ActivityPlan codec/compiler
    -> #773 pure planning scenario expectations
      -> #774 saga program/state/journal
        -> #775 pure scheduling
          -> #776 compensation/recovery planning
            -> #777 planning/saga batch closeout
```

Audit result:

```text
families:          11
laws:              74
retained families: 11
moved families:     0
split families:     0
```

The scenario-expectation family was the only subtle one. It is retained for
#773 only as a closed expectation catalogue and event partial-order language.
The actual Postgres scenario runner, DeploymentProgram workflow, coordinator,
workers, stores, Docker, FastAPI, MCP, and effect adapters remain outside #739.

The core shape to preserve is:

```text
GraphDiff
  -> ActivityPlan
    -> SagaProgram / SagaState / SagaJournalProjection
      -> ExecutionSchedule
        -> compensation/recovery planning views
```

Validation evidence:

```text
focused #771 batch/parity guard slice: 15 tests passed
./validate-parity.sh foundation:       valid=true, findings=0
control-plane-kit-core slice:          269 tests passed
./test.sh:                             1187 tests passed
git diff --check:                      clean
```

This means `ActivityPlan` remains the result of graph interpretation. It is not
a user-authored arbitrary workflow language.

## #772 ActivityPlan Codec And Compiler Mapping

#772 maps the first implementation slice of the #739 planning/saga batch.

The exact mapped families are:

```text
test_activity_plan_codec:     12 laws
test_activity_plan_compiler:   5 remaining unmapped laws
```

The compiler family has 8 frozen laws in the full parity manifest. Three were
already covered by `extract-b-614.pure-kernel.unittest`; #772 adds dedicated
extracted-core compiler tests and maps the five still-unmapped compiler laws.
The dedicated tests still exercise all 8 compiler behaviors, but the manifest
mapping is intentionally limited to the remaining unmapped work.

New successor tests:

```text
control-plane-kit-core/tests/test_activity_plan_codec.py
control-plane-kit-core/tests/test_activity_plan_compiler.py
```

New proof artifact:

```text
artifacts/extraction/successor-proofs/extract-e-772-activity-plan-codec-compiler.json
```

Important shape:

```text
GraphDiff
  -> compile_activity_plan
    -> ActivityPlan
      -> ActivityPlanDescriptorCodec
        -> deterministic durable descriptor
```

Objects:

```text
ActivityPlan
PlannedActivity
ActivityOperation
ActivityDependency
RiskLevel
ActivityImpact
CompensationSpec
GraphDiff
StructuralChange
```

Laws proved:

```text
codec variants are closed and fail on unknown schema/operation/review values;
codec output is deterministic and permutation-stable;
codec rejects arbitrary payload mappings and lossy descriptor fields;
review diagnostics carry structural subjects, not changed secret values;
risk and destructive markers survive descriptor encoding;
compiler maps graph metadata-only diffs to no runtime work;
compiler preserves startup and teardown dependency order;
environment socket bindings remain startup material, not socket effects;
runtime-control socket switches become typed SwitchSocketConnection work;
environment reconciliation precedes removal of the old provider endpoint;
runtime moves order start -> reconcile -> stop;
unsupported and ambiguous graph changes become high-risk review blockers.
```

Boundary decision:

```text
ActivityPlan is still graph-interpretation output.
It is not a user-authored arbitrary workflow language.
No stores, workers, Docker, FastAPI, MCP, coordinator, or runtime effects enter
the extracted-core mapping.
```

Validation evidence:

```text
focused #772 extracted-core successor tests: 18 tests passed
focused root parity guard slice:            20 tests passed
./validate-parity.sh foundation:            valid=true, findings=0
control-plane-kit-core slice:               287 tests passed
full Docker/Postgres ./test.sh suite:       1188 tests passed
```

## #773 Pure Planning Scenario Expectation Mapping

#773 maps the pure scenario catalogue side of the planning/saga batch.

The exact mapped families are:

```text
test_planning_scenarios:                4 successor laws + 1 reviewed supersession
test_execution_scenario_expectations:  12 successor laws
```

The superseded law is:

```text
behavior.every-scenario-runs-through-the-postgres-planning-workflow
```

That law is not lost. It is explicitly outside extracted core because it speaks
about Postgres stores, UnitOfWork, workflow services, and the durable
application boundary. It belongs to the later operations/contracts batch, not
to the pure scenario catalogue.

New successor module:

```text
control_plane_kit_core.planning.scenarios
```

New successor tests:

```text
control-plane-kit-core/tests/test_planning_scenarios.py
```

New proof artifact:

```text
artifacts/extraction/successor-proofs/extract-e-773-planning-scenario-expectations.json
artifacts/extraction/supersession-reviews/extract-e-773-planning-workflow-supersession.json
```

Important shape:

```text
PlanningScenario
  = current DeploymentGraph
  x desired DeploymentGraph
  x ScenarioExpectation[OperationExpectation, DependencyExpectation]

ExecutionScenario
  = PlanningScenario
  x ExecutionScenarioExpectation
```

Boundary decision:

```text
The scenario catalogue is pure acceptance data over the graph and planning
languages.

It is not the Postgres scenario runner.
It is not DeploymentProgram.
It is not a coordinator, Docker, FastAPI, MCP, or runtime-effect proof.
```

Implementation note:

The extracted-core catalogue uses generic topology blocks rather than
package-owned Hello, router, rate-limiter, multiplexer, or load-balancer
products. This keeps the scenarios focused on topology/planning laws and avoids
pulling server-product declarations back into core.

The contracts also follow the current ActivityPlan compiler rather than frozen
obsolete assumptions. In particular:

```text
router backend switch -> SwitchSocketConnection("api-router.active")
load-balancer scale-out -> AddSocketConnection effects wait for health
runtime move -> ReconcileNode + ReconcileRuntime operations
unsupported implementation transition -> ReviewChange only
```

Validation evidence:

```text
focused #773 extracted-core successor tests: 18 tests passed
focused root parity/supersession slice:     22 tests passed
./validate-parity.sh foundation:            valid=true, findings=0
control-plane-kit-core slice:               305 tests passed
full Docker/Postgres ./test.sh suite:       1189 tests passed
git diff --check:                           clean
```

Test-integrity note:

The first full-suite run after adding the reviewed supersession failed because
`tests/test_extraction_supersession_review.py` still encoded the old invariant
"there are no manifest supersessions." The corrected invariant is stronger and
matches the parity language:

```text
manifest supersessions
  = reviewed supersessions aggregated from supersession-review artifacts
```

This keeps supersession exceptional and reviewable without making the old
issue-732 review artifact carry future decisions.

## #774 Saga Program, State, Schedule, And Journal Mapping

#774 maps the pure saga side of the planning/saga batch.

The exact mapped families are:

```text
test_saga_program: 3 successor laws
test_saga_state:   9 successor laws
test_saga_journal: 8 successor laws
```

New successor module:

```text
control_plane_kit_core.planning.saga
```

New successor tests:

```text
control-plane-kit-core/tests/test_saga.py
```

New proof artifact:

```text
artifacts/extraction/successor-proofs/extract-e-774-saga-program-state-journal.json
```

Important shape:

```text
SagaProgram[Effect]
  = End
  | StepNode[SagaStep[Effect], SagaProgram[Effect]]
  | ParallelNode[tuple[SagaProgram[Effect], ...], SagaProgram[Effect]]

SagaState
  = tuple[SagaStepState]
  x completion_order
  x failed_steps
  x cancelled
  x compensation_requested

ActivityPlan x tuple[ActivityJournalEvent]
  -> SagaJournalProjection[SagaState, in_flight, uncertain]

ActivityPlan x SagaState
  -> ExecutionSchedule
```

Boundary decision:

The extracted module is still pure core. It owns immutable syntax, immutable
events, replay, and dependency scheduling over `ActivityPlan`. It does not own
Postgres activity events, worker leases, stores, recovery commands, adapters,
coordinator execution, Docker, FastAPI, MCP, or runtime effects.

The activity journal type introduced here is a pure closed value:

```text
ActivityJournalEvent
  = event_id
  x run_id
  x ordinal
  x ActivityJournalEventKind
  x optional activity_id
```

It is intentionally not the operations-layer store row. Later operations work
may adapt durable `ActivityEventRecord` rows into this pure value before
projection.

Implementation notes:

```text
SagaStep.effect is typed data and explicitly need not be callable.
parallel(...) rejects fewer than two branches and empty branches.
program_steps(...) rejects duplicate stable identities.
SagaCompensationRequested is replayable immutable state, not a coordinator flag.
compensation_candidates(...) follows reverse durable completion order.
project_activity_journal(...) folds canonical activity events into saga events
without creating a second journal.
derive_schedule(...) interprets ActivityPlan + SagaState without effects.
```

Test-integrity note:

The first bridge test assumed constructor order for `ActivityPlan`. That was an
obsolete structural assumption: `ActivityPlan` already canonicalizes activity
order. The corrected assertion compares saga step order to
`plan.activities`, preserving the semantic law instead of weakening it.

The first compensation-admission implementation also rejected
`RUN_COMPENSATION_STARTED` after a successfully completed one-step plan. The
journal law requires that a completed compensatable plan can enter compensation
from reconstructed state. The fix admits compensation from `SUCCEEDED` only
when durable completed compensatable work exists, so successful forward work can
be reversed without allowing arbitrary compensation admission.

Validation evidence:

```text
focused #774 extracted-core successor tests: 25 tests passed
focused #774 core closeout slice:           28 tests passed
focused root parity guard slice:            23 tests passed
./validate-parity.sh foundation:            valid=true, findings=0
control-plane-kit-core/test.sh:             330 tests passed
./test.sh:                                  1190 tests passed
git diff --check:                           clean
```

## #775 Pure Scheduling Law Mapping

#775 maps the frozen scheduling families onto the extracted core scheduler.

Mapped families:

```text
test_scheduling:           9 successor laws
test_scheduling_scenarios: 1 successor law
```

New successor tests:

```text
control-plane-kit-core/tests/test_scheduling.py
```

New proof artifact:

```text
artifacts/extraction/successor-proofs/extract-e-775-pure-scheduling-laws.json
```

Important shape:

```text
ActivityPlan x SagaState
  -> ExecutionSchedule

ExecutionSchedule
  = ready
  x running
  x waiting
  x blocked
  x succeeded
  x failed
  x compensating
  x compensated
  x compensation_failed
  x compensation_ready
```

Boundary decision:

Scheduling remains a pure core interpreter over the #774 saga state language.
It does not own worker claims, durable leases, coordinator loops, stores,
adapters, Docker effects, FastAPI, MCP, or runtime mutation. Those layers may
consume `ExecutionSchedule`, but they do not define it.

Implementation decision:

The #774 support tests for scheduling were moved out of `test_saga.py` and into
`test_scheduling.py`, then expanded to cover the complete frozen scheduling
families. This keeps saga replay and schedule derivation adjacent in the
implementation while making the law families separate and inspectable in tests.

Validation evidence:

```text
focused #775 scheduling + saga slice: 31 tests passed
focused #775 closeout slice:          13 tests passed
focused root parity guard slice:      24 tests passed
./validate-parity.sh foundation:      valid=true, findings=0
control-plane-kit-core/test.sh:       336 tests passed
./test.sh:                            1191 tests passed
git diff --check:                     clean
required incomplete core laws:        653 -> 643
```

## #776 Compensation And Recovery Planning Law Mapping

#776 maps the frozen compensation-planning and recovery-planning families into
the extracted core.

Mapped families:

```text
test_compensation_planning: 6 successor laws
test_recovery_planning:     4 successor laws
```

New successor tests:

```text
control-plane-kit-core/tests/test_compensation_planning.py
control-plane-kit-core/tests/test_recovery_planning.py
```

New proof artifact:

```text
artifacts/extraction/successor-proofs/extract-e-776-compensation-recovery-planning.json
```

Important shape:

```text
current graph x target graph
  -> GraphDiff
    -> ActivityPlan
      -> RecoveryCandidate

RecoveryCandidate
  = RecoveryMode
  x source graph identity?
  x target graph identity
  x ActivityPlan
  x ApprovalRequirement
  x tuple[RecoveryActivityAssessment]
  x tuple[RecoveryLimitation]
```

Boundary decision:

Recovery planning belongs in extracted core only as pure planning data. It
constructs fresh canonical `ActivityPlan` values from graph transitions and
attaches closed reviewable limitations. It does not import stores, UnitOfWork,
Postgres, coordinator loops, worker claims, Docker adapters, HTTP APIs, MCP, or
runtime effects.

The distinction is important:

```text
plan_recovery_transition(current, target)
  = plan current -> target
  + say what graph structure alone cannot prove

plan_reconstruction(target)
  = plan empty -> target
  + say that missing source truth is unknown
```

This is not rollback. It is a recovery candidate that can later be reviewed,
approved, admitted, and interpreted by operations/server layers.

Curated snippets:

```python
def plan_recovery_transition(
    current: ValidatedGraph,
    target: ValidatedGraph,
    *,
    approval_policy: ApprovalPolicy | None = None,
) -> RecoveryCandidate:
    _require_validated(current, "current")
    _require_validated(target, "target")
    plan = compile_activity_plan(diff_graphs(current, target))
    return _candidate(
        mode=RecoveryMode.REVERSE_TRANSITION,
        source_graph_name=current.graph.name,
        target_graph_name=target.graph.name,
        plan=plan,
        approval_policy=approval_policy,
    )
```

```python
def plan_reconstruction(
    target: ValidatedGraph,
    *,
    approval_policy: ApprovalPolicy | None = None,
) -> RecoveryCandidate:
    _require_validated(target, "target")
    empty = validate_graph(
        DeploymentGraph(f"empty:{target.graph.name}"),
        codec=target.codec,
    )
    plan = compile_activity_plan(diff_graphs(empty, target))
    return _candidate(
        mode=RecoveryMode.RECONSTRUCTION,
        source_graph_name=None,
        target_graph_name=target.graph.name,
        plan=plan,
        approval_policy=approval_policy,
    )
```

Test-integrity note:

The first recovery fixture used a frozen-package shortcut constructor for
`ApplicationBlock`. That was an obsolete structural assumption, not a behavior
law. The corrected successor fixture uses the extracted core language directly:

```text
ApplicationBlock
  = BlockSpec
  x RuntimeImplementation
  x BlockSockets
```

The behavior law stayed intact: recovery still plans between validated graphs,
records limitations, and refuses raw graph inputs.

Validation evidence:

```text
focused #776 compensation/recovery slice: 10 tests passed
focused root parity guard slice:          12 tests passed
./validate-parity.sh foundation:          valid=true, findings=0
control-plane-kit-core/test.sh:           346 tests passed
./test.sh:                                1192 tests passed
git diff --check:                         clean
required incomplete core laws:            643 -> 633
```

## #777 Planning/Saga Batch Closeout

#777 closes the #739 planning/saga mapping batch.

Closed batch:

```text
#739 planning/saga batch
  = 11 retained families
  = 74 frozen laws
```

Closeout artifact:

```text
artifacts/extraction/planning-saga-batch-closeout.json
```

Updated live inventory:

```text
artifacts/extraction/required-core-family-inventory.json
```

Closeout summary:

```text
source families:                         11
source entries:                          74
mapped retained families:                11
retained source entries:                 74
unexpected remaining retained families:   0
live required-core inventory after #777: 533 entries / 69 families
```

The mapped families are:

```text
test_activity_plan_codec
test_activity_plan_compiler
test_planning_scenarios
test_execution_scenario_expectations
test_saga_program
test_saga_state
test_saga_journal
test_scheduling
test_scheduling_scenarios
test_compensation_planning
test_recovery_planning
```

Important closeout law:

```text
planning/saga retained family
  -> passing successor evidence
    -> absent from live required-core inventory
```

This proves the batch without using aggregate suite success as a substitute for
per-family evidence. The closeout artifact is derived from:

```text
planning-saga-batch-audit.json
  x required-core-family-inventory.json
    -> planning-saga-batch-closeout.json
```

Boundary decision:

The batch remains pure:

```text
GraphDiff
  -> ActivityPlan
    -> SagaProgram / SagaState / SagaJournalProjection
      -> ExecutionSchedule
        -> compensation and recovery planning views
```

No stores, UnitOfWork, Postgres, coordinator loops, worker claims, Docker,
FastAPI, MCP, or adapter effects were moved into extracted core to satisfy
#739.

Validation evidence:

```text
focused #777 batch closeout slice: 16 tests passed
focused parity/batch slice:       28 tests passed
validate-parity.sh foundation:    valid=true, findings=0
control-plane-kit-core/test.sh:   346 tests passed
./test.sh:                        1193 tests passed
git diff --check:                 clean
```

## #785 Operations-Contract Batch Audit

#785 starts #740 by auditing the live operations-contract scope before mapping.

Source batch:

```text
artifacts/extraction/required-core-batch-plan.json
batch: operations_contract
```

Additional split source:

```text
artifacts/extraction/contract-boundary-classification.json
```

The original #737 batch assigns 33 families / 275 laws to #740. The
`test_contracts` split from #760 adds 23 mutable contract laws, so #740's
current audit scope is:

```text
families: 34
entries:  298
```

Audit artifact:

```text
artifacts/extraction/operations-contract-batch-audit.json
```

The child topology is:

```text
#785 audit operations contract batch ownership
  -> #786 DeploymentProgram stage and public boundary laws
    -> #787 command vocabulary and workflow contract laws
      -> #788 read projection and API/MCP parity contract laws
        -> #789 admission lifecycle recovery and advancement contract laws
          -> #790 execution coordinator and verification command laws
            -> #791 store UnitOfWork Postgres and mutation-holder laws
              -> #792 close operations contract mapping batch
```

Partition:

```text
#786:  4 families /  12 laws
#787:  7 families /  51 laws
#788:  8 families /  72 laws
#789:  7 families /  75 laws
#790:  2 families /  35 laws
#791:  6 families /  53 laws
```

Boundary decision:

```text
core
  owns pure operation contracts, descriptors, public command/read vocabulary,
  and service composition boundaries

operations / cpk-server
  owns durable stores, UnitOfWork, Postgres, process routes, workers, effects,
  mutable holders, and runtime execution
```

Important #785 law:

```text
operation-contract law
  -> pure contract successor
  | reviewed operations handoff
  | reviewed server/process handoff
  | reviewed interpreter/runtime handoff
  | reviewed supersession
```

The audit intentionally does not solve #740. It assigns every currently visible
family to a focused child so later mapping PRs can decide law-by-law whether a
pure core contract exists or whether the behavior must be handed to the future
operations/cpk-server package.

Validation evidence:

```text
focused #785 audit guard: 1 test passed
focused extraction batch plan: 5 tests passed
validate-parity.sh foundation: valid=true, findings=0
./test.sh: 1194 tests passed
git diff --check: clean
```

## #786 DeploymentProgram Boundary Mapping

#786 maps the public DeploymentProgram / Deploy-stage boundary slice from #740.

Mapped frozen families:

```text
test_backend_boundaries:             4 laws
test_deployment_application_values:  4 laws
test_deployment_plan_approve_stages: 3 laws
test_deployment_admit_claim_stages:  1 law

total: 4 families / 12 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-786-deployment-program-boundary.json
extract-e-786.deployment-program-boundary.unittest
sha256:858f3ae9cb3b24e29415a0a9d69dfb4a3940c4e13300f9334dbe6e0682f387ab
```

The key implementation addition is a pure stage contract, not a live workflow
executor:

```python
class DeploymentProgramStage(StrEnum):
    PLAN = "plan"
    APPROVE = "approve"
    ADMIT = "admit"
    CLAIM = "claim"
    EXECUTE = "execute"
    ADVANCE = "advance"
```

The canonical public order is explicit data:

```python
def canonical_deployment_stage_pipeline() -> DeploymentStagePipeline:
    stages = tuple(DeploymentProgramStage)
    return DeploymentStagePipeline(
        tuple(
            DeploymentStageContract(
                stage=stage,
                service_role=_STAGE_ROLES[stage],
                requires_prior_stage=None if index == 0 else stages[index - 1],
                creates_durable_handoff=True,
            )
            for index, stage in enumerate(stages)
        )
    )
```

The `creates_durable_handoff` flag is deliberately contract language, not
store behavior. It says every public stage must produce an inspectable handoff
for the next request boundary. Operations / cpk-server decide how that handoff
is persisted and authorized.

This preserves Jacob's intended public shape:

```text
Deploy(current, desired)
  -> plan
    -> approve
      -> admit
        -> claim
          -> execute
            -> advance
```

But extracted core still stops at contracts:

```text
core
  owns stage names, stage order, service-role mapping, descriptors, and
  cpk-server handoff contracts

operations / cpk-server
  owns persistence, durable lookup, approval queues, workers, coordinator
  dispatch, graph-store mutation, HTTP/MCP process routes, and runtime effects
```

Validation evidence:

```text
meaningful red: 4 #786 families still unmapped
focused #786 successor tests: 27 tests passed
focused #786 mapping guard: 1 test passed
extraction/parity mapping suite: 30 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=621
```

## #787 Command Workflow Contract Mapping

#787 maps the command vocabulary and workflow contract slice from #740. The
operations audit artifact governed the scope; although the issue body mentioned
`test_workflows`, the current artifact assigns this issue exactly six families:

```text
test_activity_planning_command_service:  9 laws
test_approval_command_service:          10 laws
test_desired_graph_command_service:      8 laws
test_desired_graph_commands:             3 laws
test_operation_command_service:         10 laws
test_operation_commands:                 8 laws

total: 6 families / 48 laws
```

`test_workflows` was not mapped here because it is not present in
`artifacts/extraction/operations-contract-batch-audit.json` for #787. It should
remain available for later operations / cpk-server handoff classification if a
future audit assigns it.

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-787-command-workflow-contract.json
extract-e-787.command-workflow-contract.unittest
sha256:0cf7e6877af2c0f4633f0bef1b992f01f6a1198770732cc81e65c103b3b73f58
```

The key implementation addition is a pure command workflow contract. It names
what commands exist and what handoff laws they obey, without introducing a
command service, store, UnitOfWork, ledger, authorization implementation, or
route executor:

```python
class OperatorCommandKind(StrEnum):
    START_OPERATION_SESSION = "start-operation-session"
    CLOSE_OPERATION_SESSION = "close-operation-session"
    CANCEL_OPERATION_SESSION = "cancel-operation-session"
    RECORD_OPERATION_ACTION = "record-operation-action"
    SET_DESIRED_GRAPH = "set-desired-graph"
    REQUEST_ACTIVITY_PLAN = "request-activity-plan"
    REQUEST_APPROVAL = "request-approval"
    DECIDE_APPROVAL = "decide-approval"
```

Each command contract is a product of closed values:

```python
@dataclass(frozen=True)
class OperatorCommandContract:
    operation_id: str
    kind: OperatorCommandKind
    family: OperatorCommandFamily
    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    idempotency: CommandIdempotencyPolicy
    approval: ApprovalPolicy
    activity_history: ActivityHistoryPolicy
    payload_policy: CommandPayloadPolicy
    requires_open_session: bool
    creates_session: bool
    terminal_session_transition: bool
```

The canonical command workflow is therefore:

```text
OperatorCommandWorkflowContract
  = [OperatorCommandContract]

OperatorCommandContract
  = command identity
  x family
  x DeploymentProgramStage
  x service role
  x request/response schema names
  x idempotency policy
  x approval relation
  x activity-history handoff
  x payload descriptor policy
  x session-shape flags
```

This preserves the boundary:

```text
core
  owns closed command identities, families, stage/service-role mapping,
  descriptor policy, idempotency/approval/history contract data, and successor
  evidence.

operations / cpk-server
  owns command services, Postgres stores, UnitOfWork, mutable operation sessions,
  command ledgers, authorization enforcement, durable replay/conflict behavior,
  approval queues, graph-store mutation, HTTP/MCP route execution, and runtime
  effects.
```

The test-integrity red evidence was meaningful:

```text
before mapping: 6 #787 subtests failed, one for each unmapped family
```

After mapping:

```text
focused #787 successor tests: 4 tests passed
adjacent command/stage focused tests: 16 tests passed
focused #787 mapping guard: 1 test passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=573
```

## #788 Read Projection And API/MCP Parity Mapping

#788 maps the read, projection, API, MCP, and operator-view parity slice from
#740. The operations audit artifact and issue body agreed on the scope:

```text
test_instance_read_service:       21 laws
test_instance_read_fastapi:       10 laws
test_mcp_read:                     8 laws
test_focused_read_hardening:       7 laws
test_focused_workflow_reads:       7 laws
test_operator_graph_projection:    5 laws
test_observation_projection:       5 laws
test_operator_recovery_projection: 9 laws

total: 8 families / 72 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-788-read-projection-contract.json
extract-e-788.read-projection-contract.unittest
sha256:d964936d20df974850dddb388e8efa83d2b193f7fbc7a914c348c664a7d3b67d
```

The key implementation addition is a pure projection contract. It names read
projection identity, response schema, redaction/evidence policy, workspace
scope, paging bounds, and read-only safety without implementing a read service,
FastAPI route, MCP server loop, store, auth middleware, or token validator:

```python
class ReadProjectionKind(StrEnum):
    WORKSPACE = "workspace"
    CURRENT_GRAPH = "current-graph"
    DESIRED_GRAPH = "desired-graph"
    OPERATOR_GRAPH = "operator-graph"
    ACTIVITY_TIMELINE = "activity-timeline"
    OPEN_SESSIONS = "open-sessions"
    SESSION_DETAIL = "session-detail"
    PLAN_DETAIL = "plan-detail"
    PENDING_APPROVALS = "pending-approvals"
    OBSERVED_STATE = "observed-state"
    CONTROL_SURFACE = "control-surface"
```

Each projection contract is a product of closed values:

```python
@dataclass(frozen=True)
class ReadProjectionContract:
    operation_id: str
    kind: ReadProjectionKind
    service_role: ControlPlaneServiceRole
    response_schema: str
    policy: ReadProjectionPolicy
    auth_scope: HttpAuthScope
    safety: HttpOperationSafety
    requires_workspace_scope: bool
    paged: bool
    max_page_size: int | None = None
```

The contract shape is:

```text
ReadProjectionSet
  = [ReadProjectionContract]

ReadProjectionContract
  = projection identity
  x projection kind
  x response schema
  x redaction/evidence policy
  x read auth scope
  x read-only safety
  x workspace-scope requirement
  x optional bounded paging
```

This preserves the boundary:

```text
core
  owns read projection identities, response schema names, redaction/evidence
  policy, workspace-scope flags, paging bounds, read-only safety, HTTP/MCP
  parity contracts, and successor evidence.

operations / cpk-server
  owns read services, stores, FastAPI app construction, MCP server loops,
  auth middleware, token validation, route handlers, service error mapping,
  and mutable projection computation.
```

The test-integrity red evidence was meaningful:

```text
before mapping: 8 #788 subtests failed, one for each unmapped family
```

After mapping:

```text
focused #788 successor tests: 4 tests passed
adjacent read/command/package focused tests: 17 tests passed
focused #788 mapping guard: 1 test passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=501
```

## #789 Admission Lifecycle Recovery And Advancement Mapping

#789 maps the admission, run lifecycle, recovery-decision, concurrency, and
current-graph advancement slice from #740. The operations audit artifact and
issue body agreed on the scope:

```text
test_execution_values:             16 laws
test_execution_admission:          11 laws
test_run_lifecycle:                21 laws
test_recovery_decisions:            8 laws
test_execution_concurrency:         5 laws
test_recovery_concurrency:          3 laws
test_current_graph_advancement:    11 laws

total: 7 families / 75 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-789-execution-lifecycle-contract.json
extract-e-789.execution-lifecycle-contract.unittest
sha256:eef908b9c5190bb3f6085c68b59e4ffd00da0ca048ef5fdd4f7949d879384c11
```

The key implementation addition is a pure execution lifecycle contract. It
names request statuses, run statuses, event kinds, event scopes, failure
categories, recovery scopes, recovery decisions, lifecycle operation
identities, transition domains, and advancement preconditions without
implementing durable claims, leases, locks, Postgres writes, graph-store
mutation, or recovery command execution:

```python
class ActivityRunStatus(StrEnum):
    CLAIMED = "claimed"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    PARTIALLY_FAILED = "partially_failed"
    UNCOMPENSATED_FAILURE = "uncompensated_failure"
    CANCELLED = "cancelled"
```

The main contract object is:

```python
@dataclass(frozen=True)
class ExecutionLifecycleContractSet:
    timing: tuple[RunStatusTimingContract, ...]
    events: tuple[ActivityEventContract, ...]
    recovery_decisions: tuple[RecoveryDecisionContract, ...]
    operations: tuple[LifecycleOperationContract, ...]
```

The algebraic shape is:

```text
ExecutionLifecycleContractSet
  = RunStatusTimingContract*
  x ActivityEventContract*
  x RecoveryDecisionContract*
  x LifecycleOperationContract*

LifecycleOperationContract
  = operation identity
  x DeploymentProgramStage
  x service role
  x request/response schema names
  x accepted run-status domain
  x result run status
  x emitted event kinds
  x approval / worker / current-graph preconditions
  x explicit enforcement owner
```

The important design decision is that concurrency laws are represented as
contract obligations, not simulated in extracted core. Every lifecycle
operation has:

```python
enforcement_owner = ContractEnforcementOwner.OPERATIONS
```

This records architectural truth instead of smuggling the Postgres
interpreter into the kernel. Core names the law; operations / cpk-server prove
it with durable serialization, UnitOfWork, worker claims, leases, and
graph-store transactions.

This preserves the boundary:

```text
core
  owns closed lifecycle/status/event/recovery identities, descriptor shapes,
  transition-domain contracts, recovery scope/precondition contracts,
  graph-advancement precondition names, and successor evidence.

operations / cpk-server
  owns Postgres stores, UnitOfWork, durable journals, locks, leases, worker
  claims, one-winner enforcement, current approval checks, recovery execution,
  authorization enforcement, current graph mutation, route handlers, and
  runtime effects.
```

The test-integrity red evidence was meaningful:

```text
before mapping: 7 #789 subtests failed, one for each unmapped family
```

After mapping:

```text
focused #789 successor tests: 6 tests passed
focused #789 mapping guard: 1 test passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=426
```

## #790 Execution Coordinator And Verification Command Mapping

#790 maps the execution coordinator and verification command slice from #740.
The issue body and live inventory agreed on the scope:

```text
test_execution_coordinator:          28 laws
test_verification_command_service:    7 laws

total: 2 families / 35 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-790-execution-coordinator-contract.json
extract-e-790.execution-coordinator-contract.unittest
sha256:6d468eefb5e530fb56224c770961255c2271886f7efeaeba358a67a42e54a4a5
```

The key implementation addition is a pure coordinator and verification contract
set. It names coordinator commands, verification commands, effect boundary
phases, result kinds, material policies, and uncertainty policies without
implementing the coordinator loop, adapter registry, store journal, worker
claim, Docker/HTTP/probe dispatch, or crash-window recovery behavior:

```python
@dataclass(frozen=True)
class ExecutionCoordinatorContractSet:
    coordinator_commands: tuple[ExecutionCoordinatorCommandContract, ...]
    verification_commands: tuple[VerificationCommandContract, ...]
    effect_boundaries: tuple[EffectBoundaryContract, ...]
    effect_result_kinds: tuple[EffectResultKind, ...]
```

The algebraic shape is:

```text
ExecutionCoordinatorContractSet
  = ExecutionCoordinatorCommandContract*
  x VerificationCommandContract*
  x EffectBoundaryContract*
  x EffectResultKind*

ExecutionCoordinatorCommandContract
  = command identity
  x execute stage
  x execution service role
  x request/response schema names
  x current approval + idempotency policy
  x pinned material policy
  x uncertainty policy
  x after-commit effect policy
  x worker and pinned-plan preconditions
  x intent/result durability obligations
  x operations-owned enforcement marker
```

The most important law is still the external-effect law:

```text
short transaction records durable intent
  -> commit
    -> external effect
      -> short transaction records result, observation, and settlement evidence
```

Core expresses that law as contract data:

```python
EffectBoundaryContract(
    boundary=EffectBoundaryKind.DISPATCH,
    external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
    durable_before_effect=True,
    durable_after_effect=False,
    may_leave_uncertainty=True,
    enforcement_owner=ContractEnforcementOwner.OPERATIONS,
)
```

Verification commands are similarly pure. They consume canonical graph/probe
descriptor material and name the durable result vocabulary, but probe execution,
observation persistence, stale-row marking, route exposure, and projection
rendering stay outside core:

```python
VerificationCommandContract(
    material_policy=EffectMaterialPolicy.CANONICAL_GRAPH_PROBE,
    result_kinds=tuple(VerificationResultKind),
    requires_graph_ownership=True,
    stale_on_graph_change=True,
    redacted_projection=True,
    unsupported_is_durable=True,
    enforcement_owner=ContractEnforcementOwner.OPERATIONS,
)
```

This preserves the boundary:

```text
core
  owns pure coordinator / verification command names, descriptor shapes,
  result vocabularies, effect boundary names, material policy names,
  uncertainty policy names, and handoff obligations.

operations / cpk-server
  owns coordinator execution, verification adapter execution, Docker, HTTP,
  filesystem, health, runtime effects, Postgres stores, UnitOfWork, durable
  journals, worker claims, observations, authorization, and crash-window
  recovery.
```

The test-integrity red evidence was meaningful:

```text
before mapping: 2 #790 subtests failed
test_execution_coordinator still had 28 unmapped laws
test_verification_command_service still had 7 unmapped laws
```

After mapping:

```text
focused #790 successor tests: 6 tests passed
focused #790 mapping guard: 1 test passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=391
```

## #791 Store UnitOfWork Postgres And Mutation Holder Mapping

#791 maps the persistence and mutation-holder slice from #740. The issue body
expected approximately 53 laws, and the live inventory confirmed the exact
scope:

```text
test_contracts:                      23 laws
test_stores:                         10 laws
test_unit_of_work:                    9 laws
test_operation_postgres_primitives:   5 laws
test_execution_schema_migration:      3 laws
test_execution_store:                 3 laws

total: 6 families / 53 laws
```

Successor evidence:

```text
artifacts/extraction/successor-proofs/extract-e-791-persistence-boundary-contract.json
extract-e-791.persistence-boundary-contract.unittest
sha256:88e255d5eda11ffd2a0f9fe377ccbf60a4d88ee517a60077096579e6ce61c3df
```

The key implementation addition is a pure persistence-boundary contract set. It
names durable store roles, store ordering policies, mutation-holder subjects,
mutation phases, handoff kinds, and failure visibility policies without moving
Postgres schemas, psycopg connections, repositories, DDL, UnitOfWork
implementations, locks, mutable holder publication, or cleanup execution into
core:

```python
@dataclass(frozen=True)
class PersistenceBoundaryContractSet:
    stores: tuple[DurableStoreContract, ...]
    mutations: tuple[MutationHolderContract, ...]
    handoffs: tuple[PersistenceHandoffContract, ...]
```

The algebraic shape is:

```text
PersistenceBoundaryContractSet
  = DurableStoreContract*
  x MutationHolderContract*
  x PersistenceHandoffContract*

DurableStoreContract
  = store role
  x ordering policy
  x transaction requirement
  x no-store-commit law
  x secret-value policy
  x descriptor-only schema name
  x operations-owned enforcement marker

MutationHolderContract
  = mutation subject
  x phase vocabulary
  x candidate identity preservation
  x no durable value publication
  x cleanup / retained-resource policy
  x operations-owned enforcement marker
```

The most important design decision is that database behavior is not simulated
or reimplemented in extracted core. Core records exactly which stores and
mutation holders exist, what shape their contracts have, and which enforcement
owner is responsible:

```python
PersistenceHandoffContract(
    kind=PersistenceHandoffKind.UNIT_OF_WORK,
    implementation_owner=ContractEnforcementOwner.OPERATIONS,
    allows_core_database_driver=False,
    allows_core_schema_ddl=False,
    requires_shared_transaction=True,
    requires_store_no_commit=True,
)
```

This preserves the boundary:

```text
core
  owns durable store role names, mutation subject and phase names, ordering
  policy names, failure visibility policy names, descriptor shapes, and
  operations-owned handoff obligations.

operations / cpk-server
  owns Postgres schemas, psycopg connections, repositories, UnitOfWork
  implementations, DDL, locks, idempotency indexes, ordinal assignment,
  mutable holder publication, cleanup execution, authorization, and route
  handlers.
```

A focused test-integrity correction happened during #791. One initial successor
test asserted that the rendered descriptor must not contain the substring
`ddl`. That was too blunt because the descriptor correctly says
`allows_core_schema_ddl: false`. The corrected test still forbids database
drivers such as `psycopg` and `sqlalchemy` and separately asserts the DDL flag
is false. This preserved the architectural law instead of hiding it.

The first artifact mapping exposed a second, more important topology correction.
The 23 mutation-holder laws in `test_contracts` were previously classified as
`move-to-operations`. That was too coarse once #791 introduced
`PersistenceBoundaryContractSet`. These laws have two halves:

```text
core half
  EnvironmentContract / RuntimeContract / DerivedResource subject identity
  mutation phase vocabulary
  descriptor law: no values and no secrets are published
  explicit operations-owned enforcement marker

operations / cpk-server half
  holder mutation
  version checks
  one-winner publication
  idempotent replay and conflict
  derived-resource build/dispose
  cleanup, rollback, retained-resource, and concurrency behavior
```

The classification artifact now records those laws as `split-boundary`, with
core successor evidence pointing to:

```text
extract-e-791.persistence-boundary-contract.unittest
```

and an explicit operations handoff to #792. The corresponding tests now enforce
that split-boundary laws are mapped in core only when they also carry the
operations handoff. This avoids both bad outcomes:

```text
bad: core absorbs mutable holder execution
bad: operations owns everything and core loses the boundary algebra
```

The test-integrity red evidence was meaningful:

```text
before mapping: 6 #791 subtests failed
test_contracts still had 23 unmapped laws
test_stores still had 10 unmapped laws
test_unit_of_work still had 9 unmapped laws
test_operation_postgres_primitives still had 5 unmapped laws
test_execution_schema_migration still had 3 unmapped laws
test_execution_store still had 3 unmapped laws
```

After mapping:

```text
focused #791 successor tests: 6 tests passed
focused core persistence / UoW / closeout tests: 14 tests passed
focused contract-boundary / successor-mapping / parity tests: 39 tests passed
control-plane-kit-core/test.sh: 374 tests passed, compileall passed, import ok
top-level ./test.sh: 1200 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=338
```

## #792 Operations Contract Batch Closeout

#792 closed the remaining #740 operations-contract mapping batch without
moving Postgres, UnitOfWork, command services, operation sessions, or graph
mutation behavior into extracted core.

The closeout found one live family still appearing in the #740 audit after
#785 through #791:

```text
test_workflows
  tests.test_workflows.WorkflowServiceTests.test_session_service_starts_and_closes_sessions
  tests.test_workflows.WorkflowServiceTests.test_action_service_preserves_session_action_order
  tests.test_workflows.WorkflowServiceTests.test_workflow_services_do_not_mutate_graph_truth
```

Those tests execute the frozen Postgres-backed workflow services. Their laws
are real, but they are not extracted-core laws. The corrected classification is:

```text
#787
  owns pure command vocabulary and command contract names

#791
  owns persistence boundary names and operations-owned enforcement markers

#792
  records the executable workflow-service behavior as operations / cpk-server
  handoff evidence
```

The closeout artifact now records the full #740 source scope:

```text
source families: 34
source entries: 298

mapped successor families: 32
mapped successor entries: 272

split-boundary families: 1
split-boundary entries: 23

reviewed operations handoff families: 1
reviewed operations handoff entries: 3

unexpected remaining families: 0
unexpected remaining entries: 0
```

`test_contracts` remains the explicit split-boundary family from #791. Core
keeps the EnvironmentContract, RuntimeContract, DerivedResource, phase, and
descriptor laws; operations / cpk-server keeps holder mutation, one-winner
publication, cleanup, rollback, retained-resource, and concurrency behavior.

`test_workflows` is now superseded in the parity manifest with a reviewed
operations handoff rather than a core successor. This is intentionally not a
passing extracted-core successor. It prevents the extraction from smuggling
operation-session execution into core while still making the old workflow
service laws visible to the future operations/server package.

Regenerated evidence:

```text
artifacts/extraction/operations-contract-batch-closeout.json
artifacts/extraction/operations-contract-batch-audit.json
artifacts/extraction/supersession-reviews/extract-e-792-workflow-service-operations-handoff.json
artifacts/extraction/required-core-closeout-report.json
artifacts/extraction/required-core-family-inventory.json
artifacts/extraction/parity-validation-report.json
```

Validation:

```text
focused extraction boundary / successor / batch / supersession / parity tests: 41 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=335
control-plane-kit-core/test.sh: 374 tests passed, compileall passed, import ok
top-level ./test.sh: 1201 tests passed
```

The first full-suite run caught one useful closeout omission:
`test_extraction_supersession_review` failed because the parity manifest had
three new `test_workflows` supersessions but the reviewed-supersession artifact
set still only contained the earlier #773 planning workflow review. The fix was
not to loosen the test. #792 now has its own supersession-review artifact for
the three workflow-service laws, recording that core supersedes only the old
ownership claim while operations / cpk-server remains responsible for the
executable behavior.

## #741 Architecture And Test-Harness Parity Ownership

#741 resolved the architecture-analysis, dependency, ownership, package-topology,
root-import, optional-dependency, scenario-boundary, read-route, deploy,
protocol, and test-integrity families without moving the architecture harness
into `control-plane-kit-core`.

The important decision is that these laws are real extraction laws, but most of
them are not core package APIs. They are neutral harness behavior that keeps the
extraction honest:

```text
frozen architecture/test-integrity laws
  -> neutral extraction harness successor evidence
    -> parity manifest completion
      -> required-core closeout inventory reduction
```

The two import-surface families remain core public behavior because they prove
that extracted core stays lightweight:

```text
test_root_api
test_optional_dependencies
```

Those still map through the same successor evidence because their executable
proof is the package-boundary harness plus import checks, not a new runtime
service.

The closeout artifact records:

```text
source families: 10
source entries: 58

mapped successor families: 10
mapped successor entries: 58

neutral harness families: 8
core import guard families: 2

reviewed supersession families: 0
reviewed supersession entries: 0

unexpected remaining families: 0
unexpected remaining entries: 0
```

No #741 law was completed by package-name churn or reviewed supersession. Every
law maps to:

```text
extract-e-741.architecture-test-harness.unittest
```

The successor proof covers the current top-level architecture tests:

```text
tests.test_architecture_analysis
tests.test_architecture_dependencies
tests.test_architecture_ownership
tests.test_architecture_test_integrity
tests.test_root_api
tests.test_architecture_scenarios
tests.test_architecture_read_routes
tests.test_architecture_deploy
tests.test_architecture_protocol
tests.test_optional_dependencies
```

This is intentionally not a claim that core owns Docker runtime behavior,
FastAPI route execution, cpk-server packaging, stores, UnitOfWork, graph
mutation, or package-owned server products. It is a claim that the extraction
continues to enforce those boundaries with reusable AST facts and architecture
policies.

Regenerated evidence:

```text
artifacts/extraction/architecture-test-harness-batch-closeout.json
artifacts/extraction/successor-proofs/extract-e-741-architecture-test-harness.json
artifacts/extraction/successor-evidence.json
artifacts/extraction/parity-manifest.json
artifacts/extraction/required-core-closeout-report.json
artifacts/extraction/required-core-family-inventory.json
artifacts/extraction/parity-validation-report.json
```

Validation:

```text
focused #741 extraction/parity tests: 39 tests passed
focused #741 architecture/test-harness proof: 58 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=277
control-plane-kit-core/test.sh: 374 tests passed, compileall passed, import ok
top-level ./test.sh: 1203 tests passed
required-core closeout inventory: 177 incomplete entries across 25 families
```

The count distinction matters. `validate-parity.sh foundation` still reports
all incomplete required migration entries, including system and Hello-owned
work. The `required-core-family-inventory.json` count is the core-closeout
blocker count that #742 and #743 continue to reduce before #731 can begin.

## #742 Validation, Packaging, And Demo Successor Evidence

#742 resolved the validation, packaging, and demo batch by separating core
release-candidate evidence from executable process/runtime demos.

The key split is:

```text
core owns
  wheel/import/package-boundary proof
  pure HTTP and MCP contract descriptors
  configuration artifact values
  secret-delivery contract values
  protocol/transport values
  verification contract values

entrypoints / cpk-server / interpreters own
  CLI process execution
  FastAPI demo server execution
  Postgres-seeded read demo process
  live Docker host-publication effects
```

This preserved the public-boundary laws without pretending extracted core
starts a server, hosts MCP, runs Docker, opens Postgres, or packages a
`cpk-server` image.

The closeout artifact records:

```text
source families: 4
source entries: 17

mapped successor entries: 7
reviewed supersession entries: 10

split families: 1
reviewed handoff families: 2
mapped validation families: 1

unexpected remaining families: 0
unexpected remaining entries: 0
```

Mapped core successor evidence:

```text
demo.configuration-artifact
demo.read-interface
demo.secret-delivery
demo.transport
demo.verification-observation
validation.package-installation
  -> extract-e-742.core-release-contracts.unittest

validation.complete-suite
  -> extract-e-742.complete-suite.validation
```

Reviewed handoff / supersession evidence:

```text
test_cli
  -> future entrypoint / cpk-server CLI client behavior

test_read_interface_demo_server
  -> future cpk-server / demo-server process behavior

demo.docker-publication
  -> #743 interpreter/runtime host-publication behavior
```

The important test-integrity point is that these reviewed handoffs are not
counted as migrated core behavior. They are completed in the parity manifest
only because the old claim that they were core behavior has been explicitly
reviewed and replaced with a precise future owner.

The first focused proof check exposed one artifact bug: the initial
`extract-e-742.core-release-contracts.unittest` command mounted the extracted
core source into a bare `python:3.14-slim` image and ran a unittest slice
without installing declared core dependencies. That failed at the strict Jinja
configuration-rendering boundary. The fix was not to remove Jinja or weaken the
test. The proof now records the dependency-aware core harness:

```text
./control-plane-kit-core/test.sh
```

That harness installs the extracted core wheel in an isolated container, runs
unittest discovery, runs compileall, and verifies `import control_plane_kit_core`
from outside the source tree.

Regenerated evidence:

```text
artifacts/extraction/validation-packaging-demo-batch-closeout.json
artifacts/extraction/successor-proofs/extract-e-742-core-release-contracts.json
artifacts/extraction/successor-proofs/extract-e-742-complete-suite-validation.json
artifacts/extraction/supersession-reviews/extract-e-742-validation-packaging-demo-handoff.json
artifacts/extraction/successor-evidence.json
artifacts/extraction/parity-manifest.json
artifacts/extraction/required-core-closeout-report.json
artifacts/extraction/required-core-family-inventory.json
artifacts/extraction/parity-validation-report.json
```

Validation:

```text
focused #742 extraction/parity tests: 29 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=260
control-plane-kit-core/test.sh: 374 tests passed, compileall passed, import ok
top-level ./test.sh: 1205 tests passed
required-core closeout inventory: 160 incomplete entries across 21 families
```

## #743 Interpreter And Runtime Successor Families

#743 resolved the final required-core inventory by reviewing the remaining
effectful families as handoffs instead of forcing interpreter/runtime behavior
into `control-plane-kit-core`.

The dry run found that the live inventory differed from the original batch
plan. Earlier handoffs had already removed pure control-route and verification
dispatch families, while two moved families were still live:

```text
test_block_control_fastapi
test_postgres_scenario_runner
```

The final #743 source scope was:

```text
source families: 21
source entries: 160
```

Classification:

```text
reviewed interpreter/runtime handoff
  families: 18
  entries: 131

reviewed cpk-server control-process handoff
  families: 2
  entries: 14

reviewed operations acceptance handoff
  families: 1
  entries: 15

mapped successor entries: 0
```

This is intentionally severe. These frozen laws are not fake or obsolete. They
are real laws for the next packages:

```text
interpreters / runtime packages own
  Docker effects
  Docker configuration materialization
  Docker secret materialization
  Docker ownership, retention, and cleanup
  probe execution adapters
  control HTTP clients and security policy
  capability interpreter dispatch
  runtime state interpretation

cpk-server owns
  executable FastAPI control routes
  authenticated runtime mutation
  observer mutation routes
  bounded process request handling

operations / cpk-server acceptance owns
  Postgres-backed scenario runner
  durable workflow services
  coordinator execution
  graph advancement
  read projection over mutable stores
```

Extracted core already owns the closed contract vocabulary those future owners
must obey:

```text
protocol and endpoint descriptors
control-route descriptors
verification contracts
probe intent values
resource lifecycle values
effect-boundary contract names
transaction and UnitOfWork boundary names
process handoff contracts
product runtime contracts
planning and execution scenario language
```

The manifest review therefore says:

```text
old assumption:
  executable runtime/interpreter behavior was counted as required extracted-core behavior

replacement:
  core keeps closed values and contracts
  future operations/interpreter/cpk-server packages keep executable behavior
```

Regenerated evidence:

```text
artifacts/extraction/interpreter-runtime-batch-closeout.json
artifacts/extraction/supersession-reviews/extract-e-743-interpreter-runtime-handoff.json
artifacts/extraction/parity-manifest.json
artifacts/extraction/required-core-closeout-report.json
artifacts/extraction/required-core-family-inventory.json
artifacts/extraction/parity-validation-report.json
```

Validation so far:

```text
focused #743 extraction/parity tests: 43 tests passed
validate-parity.sh foundation: valid=true, findings=0, incomplete_required=100
required-core closeout inventory: 0 incomplete entries across 0 families
control-plane-kit-core/test.sh: 374 tests passed, compileall passed, import ok
top-level ./test.sh: 1207 tests passed
```

#731 can now dry-run against zero required-core unmapped laws, but it should
not start automatically. The remaining `validate-parity.sh foundation`
`incomplete_required=100` are not required-core blockers; they are later
system, Hello, deferred product/server, or rollout obligations.
