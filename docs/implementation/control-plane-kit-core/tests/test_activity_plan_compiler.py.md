Source: [test_activity_plan_compiler.py](../../../../control-plane-kit-core/tests/test_activity_plan_compiler.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These tests exercise the public structural compiler through graph validation and
diffs. Existing laws cover owned startup, health, socket and ingress ordering,
teardown, reconciliation and explicit review barriers.

#1865 adds external/attached gateway and connector cases using the existing
public-ingress graph fixture. Owned-runtime cases expose the missing node-start
lookup after runtime creation; suppressing the runtime separately exposes the
connector's ingress predecessor lookup. Neither may fabricate node starts or
health waits. Remaining owned starts, their runtime/health predecessors and the
target-health -> allocation -> connector-start edges survive whenever their
activities exist. No execution or provider truth is inferred from a valid plan.
