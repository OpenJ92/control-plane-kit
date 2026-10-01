Source: [effect_attempt_fold_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py).
Maintain this companion alongside its source.

A signed-health fold reads nonlocking request/attempt locators to distinguish exact terminal replay from a fresh transition. Fresh work acquires the complete lifecycle-first health prefix before request/run/attempt/session/workspace/runtime row locks. Changed locators refuse without retry. Nested reload receives that same UoW-scoped prefix, checks current O1 membership and current signing permission, and cannot acquire lifecycle late. Existing runtime authority validation stays in fold. Terminal replay bypasses lifecycle/current key/latest-run/time/runtime checks and retains original evidence.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
