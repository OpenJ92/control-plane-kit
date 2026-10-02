Source: [workspaces.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workspaces.py).
Maintain this companion alongside its source.

Workspace creation owns one caller transaction. A fresh absent-workspace locator
is followed by the existing workspace lifecycle lock and an existence recheck
before allocating graph ID/time or writing. A concurrent winner is handled by
the existing replay branch. Prepared creation binds the same stores, live guard,
command and original empty graph. Private store helpers persist that graph, its
existing owner-generated identity projection, pointer and immutable initialization
receipt together. No provider call or independent commit is involved.

Replay preserves the current public response: today's workspace and current
graph. It separately verifies the retained original initialization and preserves
the original creator/key even if the replay command differs. Existing name
conflicts remain conflicts. Missing or corrupt origin refuses without adoption,
backfill or reset. This evidence is not claim-release or configuration-acceptance
authority; B2's advancement/current-use boundary remains separate.

E7 implementation is in progress on #1924 / draft PR #1926. The first three
owning targets establish reviewed causal red at e4b9b1f7. Green validation and
remaining rollback/concurrency/later-current/corruption/guard proofs are pending.
