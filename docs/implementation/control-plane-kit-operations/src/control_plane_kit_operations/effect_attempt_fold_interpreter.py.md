Source: [effect_attempt_fold_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py).
Maintain this companion alongside its source.

A signed-health fold reads nonlocking request/attempt locators to distinguish exact terminal replay from a fresh transition. Fresh work acquires the complete lifecycle-first health prefix before request/run/attempt/session/workspace/runtime row locks. Changed locators refuse without retry. Nested reload receives that same UoW-scoped prefix, checks current O1 membership and current signing permission, and cannot acquire lifecycle late. Existing runtime authority validation stays in fold. Terminal replay bypasses lifecycle/current key/latest-run/time/runtime checks and retains original evidence.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.

## B1 / #1923 configuration folds

An ordinary configuration fold joins its logical command's evidence ledger. A
nonlocking original locator distinguishes exact replay from a fresh transition.
Fresh transitions acquire lifecycle L before the request, ordered requested and
latest runs, attempt, and session locks. Locked truth must match the locator;
protection is checked before time, IDs or writes. Exact replay retains original
proof without acquiring fresh lifecycle permission. B1 does not authorize reuse,
claim release or provider cleanup. The focused PostgreSQL blocker test verifies
that a fresh fold waits at L before allocating IDs; full package acceptance and
independent review remain pending on PR #1925.

#1931 source checkpoint (unvalidated): D1 joins the existing fold-owned accounting after routing without resetting its prelude. Fresh ordinary configuration completion is prepared before IDs, bound to the actual terminal outcome, and inserted after event/outcome/CAS in the same UoW. Replay validates present linkage without admission or late lifecycle locking. Both cleanup execution refusals remain closed.
