Source: [control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This internal read projection turns durable session/action/plan/approval/run/event records into the existing Operations descriptors. It delegates workspace admission to the injected gate and delegates facts to stores; it is neither the HTTP/MCP authentication owner nor an execution service.

Run-event reads check the run and its execution request against the requested workspace before returning a bounded page. Session-bound details likewise verify ownership. Keep those joins when reusing a store lookup: knowing a run or plan ID is not authority to read another workspace. [the redaction helper](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/_redaction.py) and each record's descriptor determine the projection; inspect them rather than assuming any arbitrary stored field is safe to return.

Events expose structured identity/order, bounded evidence, categorical failure and optional recovery evidence. They do not read the private effect-attempt intent table. A returned event page therefore cannot reconstruct the full original runtime request, current provider state or transient grants. For the private preimage boundary see [effect_attempt_intent_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py).

Plan/approval details may derive recovery-plan descriptions from the stored base/desired graphs. That is a read projection of existing planning semantics, not execution, approval, provider inspection or autonomous recovery. Risk summaries and readiness flags likewise describe a plan; they do not confer permission.

Changes must remain compatible with the owning read-service and HTTP/MCP composition tests. Search for the public run-event/detail operation when tracing consumers: routes and protocol adapters may live outside this module. This note does not claim a live provider/history acceptance result.
