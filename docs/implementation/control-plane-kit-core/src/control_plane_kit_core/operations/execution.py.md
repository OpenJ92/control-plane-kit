Source: [control-plane-kit-core/src/control_plane_kit_core/operations/execution.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/execution.py).
Maintain this document alongside its source file. When command vocabulary, constructor laws, canonical factory choices, descriptor admission or enforcement boundaries change, verify and update this companion in the same change.

This module is execution contract data inside Core's operations namespace. It is
not the Operations package's worker or coordinator implementation. It defines
three frozen records, their aggregate contract set, strict mapping reconstruction
and a canonical factory. No scheduler, transaction, adapter, approval lookup,
probe, compensation action or restart loop runs here.

```text
closed enums + command/boundary requirements
  -> validated contract records -> sorted contract set -> descriptor
    -> from_descriptor -> validated contract records
```

## Objects and schema vocabulary

The coordinator kind enum has ready-effect execution, compensation execution,
restart resume and settlement. Verification has readiness, health and dependency
verification plus projection of a verification result. Separate enums name six
effect boundaries, six effect outcomes and six verification outcomes. Material
policy distinguishes pinned approved plan material from canonical graph probes;
uncertainty policy includes operator-required and never-blind-replay, although
coordinator command admission requires the latter.

EffectBoundaryContract records boundary kind, effect policy, durable-before/after
flags, possible uncertainty and enforcement owner. ExecutionCoordinatorCommandContract
adds operation ID, command kind, stage/service, request/response schema names,
idempotency/approval/material/uncertainty policies and worker/recording requirements.
VerificationCommandContract records its kind/service/schema names, result kinds,
material policy, graph/staleness/projection/unsupported requirements and owner.

The schema tables bind each kind to exact strings such as
ExecutionReadyEffectRequest/ExecutionEffectProgress and
VerificationProjectionRequest/VerificationProjectionResult. These are checked
names, not imported request classes, wire parsers or implementations registered
by this module. The [service vocabulary](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py)
and shared lifecycle/parity/transaction enums supply the remaining labels.

## Constructor laws versus factory choices

All boundary booleans must be exact bool values and enforcement must belong to
Operations. INSIDE_TRANSACTION effects are rejected for every boundary. DISPATCH
must use AFTER_COMMIT; INTENT must be durable-before; RESULT, OBSERVATION and
SETTLEMENT must be durable-after. Other flag combinations are not all fixed by
the constructor. In particular, it does not require every non-dispatch boundary
to forbid effects or every dispatch to carry the factory's durable-before flag.

The canonical factory chooses these six boundary rows:

| Boundary | Effect policy | Durable before | Durable after | May leave uncertainty |
| --- | --- | --- | --- | --- |
| materialization | forbidden | false | false | false |
| intent | forbidden | true | false | false |
| dispatch | after-commit | true | false | true |
| result | forbidden | false | true | true |
| observation | forbidden | false | true | false |
| settlement | forbidden | false | true | false |

These rows are canonical defaults, not additional checks implicitly performed
by every EffectBoundaryContract constructor. The
[external-effect policy](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py)
is still declarative data; no flag commits or forbids a real adapter call itself.

Coordinator commands must use execute stage/execution service, their kind's
schema names, required idempotency, current approval, pinned approved plan
material, never-blind-replay and after-commit effects. They require a worker,
pinned plan and Operations ownership. Intent recording must be true except that
settlement is exempt; result recording must be true except that restart resume
is exempt. The exemptions permit either bool, while the factory chooses false
for settlement's intent flag and resume's result flag. These distinctions matter
when accepting an independently constructed contract rather than the factory.

Verification commands must use observation service, their fixed schema names,
canonical graph probes, all current verification-result kinds and Operations
ownership. Graph ownership, staleness on graph change, redacted projection and
durable unsupported outcomes must all be true. The constructor checks those
declarations, not an actual graph or projection.

## Aggregate coverage and ordering

ExecutionCoordinatorContractSet requires tuples containing the proper record or
enum types. Kind sets must cover every current coordinator kind, verification
kind, effect boundary and effect result. Verification records likewise require
set coverage of all verification results.

Coverage is not multiplicity. Duplicate operation IDs are rejected separately
within coordinator and verification families, but a repeated command kind with
distinct IDs is not rejected on that basis. Boundary kinds and result kinds are
not individually deduplicated. Verification result tuples can likewise repeat a
kind while preserving their required set. No global operation-ID uniqueness is
checked across the two command families. These are source-level admission limits,
not executed duplicate-input findings or demonstrated service defects.

Commands are sorted by operation ID, boundaries by kind value and effect results
by value. Sorting does not erase duplicates. The canonical factory emits one
record per command/boundary kind and every result kind once, using execution.*
and verification.* operation IDs. Arbitrary admitted operation IDs need only be
nonempty strings without surrounding whitespace; they need not follow those
prefixes or have a fixed size bound.

## Descriptor and error boundary

Each record has an exact descriptor key set and serializes enum values and bools.
The aggregate adds kind=execution-coordinator-contract-set and four arrays. Decode
requires that tag/key set, list-valued collections, typed text/bool fields and
nested mapping records, then reconstructs the dataclasses so their semantic laws
run again. This is mapping reconstruction, not a canonical JSON/signing format,
raw parser, callback registry or executable command language.

There is no aggregate byte/item/depth cap or generic secret filter in this module.
Text admission allows normalized operation IDs and exact known schema strings;
descriptor errors may include field/enum/duplicate values. Nested from_descriptor
paths wrap ValueErrors using str(error) and retain them as causes. Callers must
not assume the categorical, cause-free error contract used by the node-control
wire modules. Exceptions from arbitrary mapping callbacks or other unexpected
types are not universally translated either.

## Interpretation and evidence

The policy requirement and the fact that satisfies it are separate. A
requires-current-approval enum is not an approval record; requires-worker is not
a lease; durable-before-effect is not a persisted intent; redacted-projection is
not a redactor. Operations must enforce those requirements with its durable
services and external interpreters. Compensation/resume vocabulary supplies no
permission for destructive cleanup or blind replay of an uncertain effect.

The [six-test contract suite](../../../../../../control-plane-kit-core/tests/test_execution_coordinator_contract.py)
checks current enum-set coverage, a mapping round trip, two open descriptor
negatives, selected boundary/command flags and two semantic contradictions. It
does not isolate every constructor guard, prove duplicate rejection everywhere,
audit error disclosure or execute a coordinator. Selected source-reference scans
found this factory/set used by Core facades and that test; they do not establish
that a downstream runtime consults this exact contract object.

Authoring read the full 917-line owner and full 180-line governing test, refreshed
aggregate checks, inspected actual service/shared-policy enum definitions and
facade bindings, and scanned selected Core/Operations references. Only this owner
receives coverage. No application imports, executable tests, persistence, approval
decisions, provider effects or live retries were performed. Future changes should
keep factory defaults, admission laws and implementation evidence distinguishable
so a validated description is never reported as an executed outcome.
