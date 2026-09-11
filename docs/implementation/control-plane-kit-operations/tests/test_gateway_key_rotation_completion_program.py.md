Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_completion_program.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_completion_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests exercise the
[completion program](../src/control_plane_kit_operations/gateway_key_rotation_completion_program.py.md)
using real PostgreSQL operations services and an in-memory revocation adapter.
The inherited [retirement fixture](gateway_rotation_retirement_fixture.py.md)
installs/verifies and truncates/seeds an isolated test database, simulates accepted
overlap, directly activates B in the key store and records drain transitions.
This file registers old-key custody, prepares retirement and simulates all of its
planned activities through the real execution program, requiring one retirement
CURRENT_GRAPH_ADVANCED event and RETIREMENT_READY. Setup does not exercise real
key generation, the activation program or a provider/gateway network.

Custody setup uses the registration service with synthetic endpoint/credential
references, the gateway-signing intent and metadata version-a / number 1 on the
old key's reference. Completion uses a fixed 06:00 textual clock and epoch 6000,
the captured retirement-ready version and four rotate/retire/key-revoke/provider-
revoke scopes. scopes or defaults means an empty tuple would restore authority;
the negative case uses the nonempty rotate-only tuple. No database tests or
provider actions were executed for this documentation.

ReplaySafeRevocationAdapter records each grant. Explicit queued results or
exceptions are returned/raised before its normal simulated mutation logic.
Otherwise it caches receipts by correlation_id, changes version-a or version-b
from active to revoked once, and returns a typed exact-version receipt. Repeating
the correlation returns the cached receipt. This cache survives fresh program
instances because the tests reuse the same adapter object; it is not durable
provider storage across an actual process restart. The cache itself does not
compare every changed grant field. Exact grant equality is asserted in selected
retry tests, and the program separately checks receipt identity.

The happy path requires COMPLETED rotation/result, one adapter call and one
simulated mutation. The grant must name the old reference and version-a / 1;
version-a becomes revoked and version-b remains active. The returned receipt
matches the grant, local old key is REVOKED and active key remains key-b. A public
rotation read-model repr must not contain the substring secret. That narrow
assertion does not audit full grants/results, arbitrary sensitive strings or
exception logs. A fresh program call returns COMPLETED_REPLAY without another
call or mutation; source shows its receipt is reconstructed from the checkpoint,
not loaded from saved provider evidence.

Definite failure first returns RETRYABLE with REVOCATION_PREPARED rotation and
RETIRED local old key. A second call succeeds; both recorded grants must compare
equal and only one simulated mutation occurs. The queued failure bypasses the
adapter's mutation path, modeling definite failure before mutation. The test does
not independently verify that an arbitrary real provider's failure classification
has that meaning, nor require a durable record of the failed attempt.

Two cases cover returned uncertainty and a typed revoked receipt with mismatched
revocation/provider IDs. Each resets truth, invokes the program and requires
BLOCKED, local old key RETIRED and no old_secret_revoked_at. These cases do not
assert exact failure-code strings, replay a blocked command, inspect all history
or simulate a provider that mutated before returning uncertainty. They establish
that these returned outcomes do not mark public key/rotation revocation complete;
they do not prove compensation or absence of provider mutation.

The invalid-input test first removes provider version metadata and requires
conflict, old key still VERIFY_ONLY and no adapter calls. Separate resets then
test an incorrect expected retirement-ready version and rotate-only scopes, each
with no adapter dispatch. Despite the test name's lineage wording, it does not
mutate accepted graph/pointers or active-key truth. It also does not assert old-key
state for every case or absence of all database reads/writes. Inactive custody,
changed provider/version/actor after preparation, mismatched public graph content
and fresh exact-revocation approval are outside these assertions.

The post-commit interruption test iterates crash indices 1..13, using the shared
CrashAfterCommitUnitOfWork to raise BaseException after a real successful physical
commit. On each caught loss it retries with fresh program/UoW objects and the same
adapter, requiring completed or completed-replay, one simulated mutation and
COMPLETED stored rotation. It breaks on an invocation that does not crash and
finally checks only that the last control observed more than five commits. It
does not explicitly assert that all 13 indices crashed or compare every retained
transition/checkpoint/key timestamp.

The loop bounds are not full completion-path coverage. Source counting includes
read-only UoW commits: on the fixture's fresh happy path, local key retirement is
commit 5, OLD_KEY_RETIRED is 7, REVOCATION_PREPARED is 10, final pre-dispatch
custody read is 12 and the first post-provider rotation read is 13. The subsequent
truth/key reads, local REVOKED write and final COMPLETED transition occur after
that tested range. Thus this loop does not establish recovery after every late
success fold or after local key revocation has committed. This is a source-level
coverage boundary, not newly executed crash evidence.

The final test's adapter mutates and caches the successful receipt, then raises
SimulatedProcessLoss before returning it on the first call. Recovery invokes the
adapter again with an exactly equal grant, obtains its cached receipt and completes.
Two calls but one mutation are asserted. This specifically proves the composed
behavior with that surviving replay cache, not exactly-once dispatch or durable
idempotency in a real provider. The program does not catch the thrown exception
or journal the attempt; it differs from returned UNCERTAIN, which blocks.

Missing cases include ordinary adapter exceptions, malformed result objects,
constructor-negative inputs, changed custody/fingerprint on retry, concurrent
completers, current/active-key drift during IO, blocked replay with a different
expected version, completed replay after external truth changes and failures after
the final local revoke/rotation commits. Tests provide no real network identity,
provider receipt authenticity, reference-registration cleanup or secret-erasure
proof. No additional exact-version approval request/decision is exercised here;
prior rotation approvals and the four focused scopes are the composed inputs.

Read depth: full 414-line test and 674-line owner, retaining full retirement366
and overlap620 fixtures and reviewed preparation/execution/activation/rotation
contexts. Actual crash wrapper, Core grant/receipt, key/provider selectors,
rotation approval/transition and revocation persistence were inspected. The
fixture's real database mutation and simulated adapter must remain distinct from
live acceptance evidence. This documentation introduces no security surface;
no source edits, executable validation, credentials, database/provider/runtime
actions or cleanup were performed.
