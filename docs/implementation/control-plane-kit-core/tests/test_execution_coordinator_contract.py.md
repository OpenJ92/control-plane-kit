Source: [control-plane-kit-core/tests/test_execution_coordinator_contract.py](../../../../control-plane-kit-core/tests/test_execution_coordinator_contract.py).
Maintain this document alongside its source file. When coordinator/verification vocabulary, boundary policies, canonical defaults or assertion limits change, verify and update this companion in the same change.

This 180-line suite has six tests for the canonical execution-coordinator contract
set. It imports the Core operations facade, constructs contract values and decodes
descriptors. It does not create a worker, open a transaction, persist intent,
dispatch an adapter, probe a graph or resume an interrupted effect.

## Vocabulary and representation

The first test compares coordinator-command kinds, verification-command kinds and
effect-result kinds with their current enum sets. It also compares the union of
all verification commands' result kinds with the current verification-result enum.
These set assertions ignore ordering and duplicates. The union assertion does
not individually require every verification command to cover every result kind;
that stronger rule exists in the actual constructor. The tests follow the current
enums rather than pinning their full literal spellings against future additions.

The descriptor test round-trips the canonical value through
ExecutionCoordinatorContractSet.from_descriptor. It rejects one extra top-level
callback field and one unknown effect-result string. This is a computed mapping
round trip with two negative cases, not a fixed canonical byte/hash oracle or an
exhaustive missing-key, wrong-type or nested-schema matrix.

The actual [contract owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/execution.py)
constructs four coordinator commands, four verification commands, six effect
boundaries and six effect-result kinds. It normalizes order, requires complete
kind coverage and rejects duplicate operation IDs within each command family.
Those constructor checks provide source context; this suite does not isolate
each of them with a negative example.

## Effect and command requirements are values

The boundary test selects DISPATCH, INTENT and RESULT by kind. Dispatch must say
after-commit, durable-before-effect and possible uncertainty. Intent must say
durable-before and not durable-after. Result must say durable-after and possible
uncertainty. Every listed boundary must name Operations as enforcement owner.
The test does not explicitly fix every field on all six boundaries or demonstrate
transaction ordering against a database.

For every coordinator command, another test checks Operations ownership, worker
and pinned-plan requirements, PINNED_APPROVED_PLAN material, the literal
never-blind-replay policy and AFTER_COMMIT effects. The three selected effect
commands—ready execution, compensation execution and restart resume—must record
intent before effects. It does not assert that settlement's flag is false or
check every command's records-result-after-effect field.

Actual constructors additionally require execute stage, execution service,
kind-specific request/response schema names, required idempotency and current
approval. The canonical factory sets these defaults. Those declarations do not
establish that a live approval exists, a worker owns a lease or any effect result
was durably recorded. The
[external-effect policy enum](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py)
names the intended boundary; it does not commit a transaction when compared.

For every verification command, the fifth test checks Operations ownership,
CANONICAL_GRAPH_PROBE material, graph ownership, staleness on graph change,
redacted projection and durable unsupported outcomes. These are boolean/enum
contract requirements. No secret-bearing projection is redacted, graph revision
is changed or unsupported probe outcome is stored by this test.

## Two constructor contradictions

The final test changes the first canonical coordinator descriptor to
INSIDE_TRANSACTION and expects InvalidExecutionCoordinatorContract during decode.
It separately sets the first verification descriptor's unsupported-is-durable
flag to false and expects the same error. The canonical set sorts operation IDs,
so these examples select compensation execution and verification projection;
they do not repeat the contradiction for every command kind.

Both negatives travel through nested decoders and the relevant constructors.
They establish local rejection before a contract value is accepted, not a live
transaction or persistence safeguard. No assertions inspect exception text,
redaction, length or cause/context chains. The owner currently wraps selected
ValueErrors with their text and cause, so this suite must not be described as
proving the cause-free diagnostic contract used by other Core languages.

## Evidence and ownership limits

The six tests support a declarative handoff:

```text
canonical contract data -> strict reconstruction -> accepted contract values
  -> Operations is named as the enforcement owner
```

They contain no adapter, database, provider, recovery or concurrency evidence.
Flags for compensation and restart do not authorize cleanup or ambiguous replay.
The suite also does not audit every facade export or the package dependency graph.

Authoring read the full test and full 917-line execution contract owner, selected
actual shared lifecycle/parity/transaction policy enums and operations facade
bindings. Only this test's row gains coverage. No application imports, executable
tests, transactions, signing or provider actions were run while authoring. Changes
to these declarations still need implementation evidence at the service that owns
the durable facts and effects; passing contract-value tests alone cannot supply it.
