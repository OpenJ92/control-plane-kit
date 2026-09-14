Source: [control-plane-kit-core/tests/test_saga.py](../../../../control-plane-kit-core/tests/test_saga.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Saga syntax and replay witnesses

DemoEffect values teach sequential composition, immutable append, parallel
branches with one continuation, and duplicate-ID rejection. The fixture's
non-callable effect assertion describes that example; it does not prove that
the generic SagaStep constructor rejects callables.

Lifecycle tests apply decide then evolve, and separately reconstruct event
tuples. They protect ordered completion, reverse compensation after parallel
completion, explicit compensation intent, retained in-flight evidence, partial
failure, compensation failure and selected impossible transitions. These are
pure state laws; they do not enforce provider execution order or supply user
approval. The ActivityPlan bridge checks IDs and compensation availability.

Journal tests cover success, run compensation intent, unsupported work before
or after start, uncertainty retained outside in_flight, resolution and
abandonment for both forward and compensation phases. Negative sequences reject
wrong-phase, missing, repeated and already-resolved abandonment, foreign steps,
mixed runs and decreasing ordinals. Abandoned forward work becomes failed
control truth and is excluded from successful-work compensation; no test
establishes that its external effect did not happen.

Full 660-line file and fixtures read. The suite does not exhaust direct
constructor validation, duplicate event IDs, ordinal gaps, ignored unscoped
events or all malformed uncertainty sequences. Scheduling has its own
[test owner](test_scheduling.py.md). See the
[saga implementation](../src/control_plane_kit_core/planning/saga.py.md) for
the distinction between syntax, state replay, schedule and journal side data.
No durability, concurrency, provider or live recovery is exercised.
