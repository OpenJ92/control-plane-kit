Source: [control-plane-kit-core/tests/test_execution_lifecycle_contract.py](../../../../control-plane-kit-core/tests/test_execution_lifecycle_contract.py).
Maintain this document alongside its source file. When lifecycle vocabulary,
claim-authority predicates or assertion boundaries change, update this companion
in the same change.

This 519-line suite contains thirteen tests of pure execution lifecycle contract
values through the Core operations facade. It describes the handoff to durable
Operations enforcement; it does not run a lifecycle, acquire a lease, record an
event, advance a graph or call a provider.

## Closed vocabulary and descriptors

The first test compares request statuses and failure categories with their current
enum order, and timing statuses, event kinds and recovery kinds with their current
enums sorted by value. Operation kinds are compared as sets, which ignores order
and duplicates. These assertions follow the current enums rather than freezing
every literal against future additions. A lowercase repr of the canonical
descriptor excludes nine implementation/security words. This is an assertion
about benign metadata, not an injected-secret redaction or dependency audit.

The second test round-trips the canonical mapping and rejects an extra top-level
key, an invented request status and an invented nested event kind. It checks the
exception class, without an exhaustive missing-key/type matrix or fixed wire
bytes and hashes. The actual
[lifecycle owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py)
supplies the factory, lookups, constructors and descriptor decoders.

## Events, timing and selected transitions

Every current event kind is compared with an expectation derived from whether its
value starts with `step_`: activity scope for steps, run scope otherwise. The test
checks both the scope helper and contract, the derived requires-activity-ID flag,
and recovery-payload permission only for RECOVERY_DECISION_RECORDED. One
RUN_STARTED descriptor falsely requiring an activity ID is rejected. No actual
event payload or journal entry is created.

Two uncertainty-abandonment events are separately pinned by literal name/value.
Both must be activity-scoped, permit neither failure nor recovery payload, and
round-trip through their mapping decoder. This does not establish that an
abandonment was authorized or any uncertain resource was removed.

A literal ten-status table fixes requires-started/requires-settled flags:
CLAIMED is false/false; RUNNING, PAUSED, FAILED and COMPENSATING are true/false;
SUCCEEDED, COMPENSATED, PARTIALLY_FAILED and UNCOMPENSATED_FAILURE are true/true;
CANCELLED is false/true. Three operation lookups pin accepted statuses for start
(CLAIMED), fail (RUNNING, PAUSED) and cancel (CLAIMED, RUNNING, PAUSED).
These are metadata assertions, not timestamps or executed state transitions.

## Recovery and claim authority

Selected recovery choices assert uncertainty-resolution scope, uncertainty/no-
uncertainty predicates, compensation availability and remaining-paused intent
matching. The three expired-claim decisions assert their respective renewal,
takeover and abandonment scopes and expired-claim requirement. This is selected
field coverage, not a complete literal matrix for every recovery decision.

Active renewal separately requires CLAIMED and an unexpired, non-expired claim.
The recovery.decide operation accepts CLAIMED, PAUSED, FAILED and PARTIALLY_FAILED.
Retry-as-new-run separately requires OPERATE scope, FAILED, no uncertainty and an
unexpired, non-expired claim, and its descriptor round-trips.

A five-entry table fixes status/unexpired/expired tuples for retry, active renewal
and the three expired-claim decisions. The test visits every canonical decision;
for decisions outside that table its expected status tuple comes from the decision
itself, while both expiry requirements must be false. That default is not an
independent status oracle for the other decisions.

Fourteen direct-construction negatives vary scope, status and expiry predicates,
including integer `1` instead of a boolean and expiry flags on REMAIN_PAUSED.
Some candidates contradict more than one requirement. A separate test fixes the
exact message `claim-authority decision has invalid status or expiry requirement`
for retry with CLAIMED status. It supplies no secret canary and does not inspect
repr, diagnostic length or exception chains.

Fifteen descriptor negatives cover a missing unexpired-claim field, selected
scope/status/predicate contradictions, an integer boolean, an invented kind and
an extra field across active renewal, expired renewal and retry. They assert the
nominal error class, not all keys and types. The actual recovery constructor
enforces its claim-authority table after scope, tuple and boolean checks; its
decoder wraps selected ValueErrors with their text and cause. These tests do not
prove cause-free public diagnostics or lease-clock enforcement.

## Durable handoff and evidence limits

The final test requires every canonical operation to name Operations as its
enforcement owner and require current approval. Filtering operations that write
the current graph must yield exactly `graph.advance-current`. That operation
requires worker scope, a current-graph match and CURRENT_GRAPH_ADVANCED as its
event tuple. No actual authorization, transaction, concurrent lease or graph
write is exercised.

The structure under test is closed contract data transformed into descriptors
and reconstructed values, with declared requirements for a later interpreter or
durable service. It is not a second implementation of that service's state
machine. Security evidence here covers selected contract admission and authority
metadata; it does not authorize retry, compensation, cleanup or external effects.

Authoring read all thirteen tests and selected actual owner sections for event
construction, recovery predicates and decoding, lookups, aggregate descriptors,
the canonical factory and its timing/event/claim tables. This does not claim a
full read of the 1,301-line owner; only this test row gains coverage. No application
imports, executable tests, builds or live operations were performed. Validation
is limited to documentation links, whitespace and the frozen source/test guard.
