Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_preparation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_preparation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This helper composes the ordinary session, projection publication, planning,
execution admission and run lifecycle services for one rotation child. It returns
a prepared checkpoint and execution handoff. The
[overlap program](gateway_key_rotation_overlap_program.py.md) and
[retirement program](gateway_key_rotation_retirement_program.py.md) supply typed
commands, phase prerequisites, deterministic prefix, actual services and a
projection-command callback. They own the final fenced rotation transition and
the preparation result/replay classification. This helper neither executes a
runtime activity nor records that final rotation checkpoint itself.

GatewayKeyRotationPreparedChild is a frozen two-field dataclass with checkpoint
and handoff annotations, without its own constructor validation. The helper
constructs actual checkpoint/handoff values whose imported constructors validate
their local contracts. The public function accepts Any for commands/services and
does not independently type-check those dependencies, authorize the actor or
prove their returned values are durable truth. Guarantees described below rely
on the actual composed services used by the two reviewed programs.

First it starts an operation session in the rotation workspace, with caller actor,
a phase-specific deployment title and rotation/phase metadata. Next it passes
the returned session ID to the callback and executes that publication command.
Planning uses the session/workspace/actor plus publication evidence: expected
current and desired authored IDs are both publication.authored_graph_id; current
realized is publication.previous_realized_projection_id; desired realized and
revision come from publication's result. It does not independently reconstruct
or compare the publication graph here; planning validates its own durable inputs.

Admission uses the resulting plan, rotation approval-request ID, actor scopes and
session/workspace. ClaimAndOpenActivityRun then uses the admitted request ID,
worker authority and requested lease duration. Absence of returned claim evidence
raises ValueError. StartActivityRun uses that claim's run and fence with the same
worker authority. After start, the helper requires an approval decision ID whose
type is str; stronger identifier/linkage conditions belong to imported values and
services. The helper has no catch, retry loop or universal error-redaction layer.

The checkpoint is PREPARED for the supplied phase. Its session/plan IDs come from
those service results, approval request/decision and execution request from
admission, run ID and prepared_at from the started run/event, and base/desired
graph/projection/revision from the plan record. The handoff combines rotation ID,
that same checkpoint and the claim fence. These coordinates expose the ordinary
child for subsequent fenced execution; they do not indicate successful activity
effects, current graph advancement, gateway readiness or rotation acceptance.

The helper appends :session, :plan, :admission, :claim and :start to a caller-supplied
prefix. It neither computes that prefix nor verifies its relationship to phase
or rotation. The actual outer programs use gkrot-overlap or gkrot-retirement plus
the SHA256 of rotation ID; their callbacks use :projection and their final
rotation transitions use :prepared. Phase separation and downstream command
fingerprints jointly support retries. Fixed strings alone are not sufficient
authority or a guarantee that changed commands replay successfully.

There is no enclosing transaction across this sequence. With the actual services,
the child stages commit separately: session, publication, plan, admission, claim
and start. The outer programs additionally perform an initial rotation read and
the final fenced checkpoint transition, producing the eight boundaries exercised
by their interruption tests. Failure after publication may retain desired material
and its history even if admission never succeeds; failure after claim/start may
leave a child run before the rotation stores its checkpoint. The helper contains
no compensation, deletion or pointer rewind for those durable prefixes.

Recovery re-enters the ordinary services with stable stage identities and depends
on each service's receipt, current-truth and authority rules. The outer programs'
prepared replay can instead return a stored checkpoint/current handoff without
calling this helper. This function does not choose that branch, renew a lease,
take over another worker's claim, or independently classify already-advanced
rotation states. Retrying with another actor, lease duration, worker or changed
child truth is governed by those service and outer-program contracts, not a
blanket idempotency promise from the shared function.

Phase validation remains outside the mechanical sequence. Overlap's program
requires KEY_GENERATED and non-null approval IDs; retirement's requires
DRAINING_OLD_GRANTS and a reached persisted deadline before child construction.
Retirement publication and the final rotation fold have their own deadline
checks. The supplied
[projection/publication mechanics](gateway_key_rotation_projection.py.md)
validate exact A+B or B material; admission checks the approved ordinary plan
against durable review and rotation truth. The helper passes approval/scopes and
worker evidence through, rather than replacing those policy boundaries.

Two source-confirmed projection limits can affect composition. Retirement compares
the Core-sorted current keys to an unsorted old/new role tuple, so reverse lexical
key order can reject valid overlap material after a session has committed.
Conditional equivalent phase material already stored under a different physical
projection ID can be reused during publication, then fail admission's canonical
ID requirement after publication/planning. Reconstructed publication replay can
also fingerprint differently. Routine public provenance of that preexisting
different-ID row is unestablished; neither caveat was executed or fixed here.

History is supplied by the composed services: session intent/metadata, publication
and planning evidence, admission decision/request, lease/run and lifecycle events.
The final rotation transition belongs to the outer program. The checkpoint and
handoff make their linkage inspectable but are not a separate atomic audit ledger.
No provider adapter, private key resolver or network listener is called here;
actual use does mutate durable Operations state. Outer interfaces must authenticate
actor/worker scope claims and protect operational references/history. Exceptions
from callbacks/services propagate for the phase program to handle; the helper
does not guarantee every message is bounded or redacted.

Reviewed [overlap preparation tests](../../tests/test_gateway_key_rotation_overlap_program.py.md)
and [retirement preparation tests](../../tests/test_gateway_key_rotation_retirement_program.py.md)
exercise this composition through real PostgreSQL services. They assert prepared
coordinates/handoff and stable-truth replay, and simulate process loss after
physical commits 1..8 with fresh-program recovery and child-count checks.
Retirement starts with a seeded overlap child, so its count of two plans/requests/
runs means one child per phase. These tests do not establish every retained
identity across all interruption cases, arbitrary injected-service correctness,
concurrent preparation, lease expiry/takeover or provider behavior. Missing-scope
and malformed-lineage assertions have phase-specific limits documented with each
test; they do not imply every rejection leaves all prior stages absent.

Read depth: full 145-line helper, retaining full overlap495/test290 and
retirement470/test152, fixture620/366 and projection/publication/rotation context.
Actual call sites, selected session/planning/admission and lifecycle claim/start/
replay contracts were inspected. The shared helper has no separately claimed
direct-test suite here; reviewed composed tests retain their own scope. No tests,
source changes, credentials, database/provider/runtime actions or publication
were performed for this documentation; it introduces no security surface.
