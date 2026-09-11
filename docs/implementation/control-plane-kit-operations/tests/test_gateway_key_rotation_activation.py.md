Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_activation.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_activation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests cover
[activation and grant-drain state](../src/control_plane_kit_operations/gateway_key_rotation_activation.py.md)
using real PostgreSQL and Operations services. setUp requires the operations test
database, installs/verifies schema, sets an injected epoch to 1000 and resets
truth through TRUNCATE workspaces CASCADE. Tests require an isolated database;
none were executed for this documentation change.

Reset uses the [shared overlap fixture](gateway_rotation_overlap_fixture.py.md)
for product-backed graph/key state, adds real provider/reference admission rows,
creates actual rotation approval records and prepares/accepts overlap through
the canonical programs. The overlap runtime uses the fixture's SuccessfulAdapter.
Its public keys, endpoint and credential references are synthetic values. Neither
setup nor activation calls a secret provider or proves cryptographic signing.
The epoch used for activation is independent of earlier preparation/execution
epochs and all textual timestamps, so these tests do not establish wall-clock
chronology between those domains.

Reference setup registers workspace-secrets with a key-prefix allowance and
GATEWAY_PROBE_SIGNING_KEY intent, then admits key-a and key-b handles and retains
B's registration ID. It does not create provider-held secret versions. The
command uses expected accepted-overlap version and operator-a, defaulting to
rotate/activate scopes. scopes or defaults means an empty tuple would restore
permissions; the actual denial test uses a nonempty PLAN_EXECUTE-only tuple.
It therefore does not separately prove denial of each missing required scope.

The main test requires waiting, draining-old-grants state, observed epoch 1000
and deadline 1065 from the fixture's 60-second lifetime plus 5-second skew. It
checks A verify-only/B active and exactly the two activation/draining transition
targets. At epoch 1064 it still waits; at exactly 1065 it becomes ready for
retirement without changing the deadline or selected row counts. This is a
boundary law for the local time comparison, with no sleep, real grant issuance,
signature validation, consumer drain observation or retirement deployment.

The restart-named test raises SimulatedProcessLoss after commits 2, 3 and 4:
key activation, new-key-active fold and draining fold, following the snapshot
read commit. The shared wrapper raises BaseException after physical commit.
A fresh program/UoW must return waiting with the same 1065 deadline, final key
roles and two selected transitions. It does not kill a process or interrupt a
database commit. Epoch/text time stay constant, so it does not cover delayed
recovery setting the first rotation deadline later than actual key activation,
nor all time-varying retry fingerprints or clock failures.

Permission and stale expected-version cases assert denial/conflict and selected
counts (zero activation/draining transitions, two key rows). A revoked B reference
causes conflict and preserves active-A/verify-B. Revoking B's key record causes
conflict before rotation folds, with the same selected counts. These test
current reference/key rejection; they do not revoke the provider itself or race
reference/key/workspace changes between the separate snapshot and mutation UoWs.

The concurrency test starts two worker threads at a Barrier before each whole
progress call. Both use the same actor, constant textual clock and epoch, then
must return equal rotations and waiting outcomes with two transitions/two key
rows. The barrier does not force a specific interleaving inside database stages.
This supports convergence under those inputs; it does not show that differing
actors or timestamps sharing the fixed transition IDs always replay. The actual
rotation fingerprint includes those fields, so such invocations can conflict.

_key_statuses reads only active/verify-only keys, excluding revoked/retired rows.
_transition_targets filters only the two activation/draining destinations.
_write_counts counts those transitions plus all signing-key rows in workspace-a;
it is not a SQL write counter and does not compare every key field, action,
event or table. Claims about no additional writes or preserved database truth
must stay limited to these assertions and the inspected source.

Other unexercised boundaries include malformed command/result clocks, result
deadline versus rotation deadline, accepted checkpoint/action-event drift,
changed desired lineage, extra verification keys, provider-version mismatches,
later unsupported rotation phases and nonmonotonic epoch observations. The
program's accepted-overlap coordinate checks and reference admission are not
fresh live-gateway or provider-custody verification.

Read depth: full 364-line tests and 427-line owner, with retained full fixture,
rotation/signing-key/store/secret-reference and overlap preparation/execution
context. Actual imported activation, locking, active-reference and deadline/
transition replay paths were rechecked. This documentation records source and
assertion scope; it adds no executed validation or security/mutation surface.
No credentials, provider keys, database, runtime or external effects were used.
