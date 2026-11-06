Source: [records.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
Maintain this companion alongside its source.

`ActivityPlanRecord.derivation_profile` is an optional, keyword-only closed
`PlanDerivationProfile`. None represents legacy absence. Existing positional
constructors retain their meaning; strings and booleans cannot impersonate the
typed profile. Current planning services explicitly choose a profile instead of
relying on this compatibility default.

The field declares reproducible semantics for the existing pinned graph pair.
It does not replace graph lineage, plan identity, action evidence, status or
approval. Stores retain its presence; planning results and replay bind it to
the corresponding planning action before deriving a canonical plan. No existing
record, graph, action or uncertain attempt is rewritten.
