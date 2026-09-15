Source: [plan_derivation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/plan_derivation.py).
Maintain this companion alongside its source.

This pure Operations module declares which interpretation defines a stored plan.
`PlanDerivationProfile.STRUCTURAL_V1` calls Core's existing structural compiler on
`transition.diff`; `MANAGEMENT_GRAPH_PAIR_V1` calls Core's accepted compiler on
`transition.current` and `transition.desired`. Historical absence (`None`) has
explicit structural meaning. Selection happens before compilation; there is no
comparison-driven fallback. The existing `Deploy` language owns classification.

Typed APIs require the exact closed enum or historical None. Wire/action values
require plain declared strings. Action key presence matters: only absent record
profile plus an absent action key is legacy. Explicit null, unequal markers or
one-sided presence fail even when the compiled plans would be equal.

Legacy stored wire remains the exact bare Core activity-plan descriptor. Explicit
profiles use the Operations-owned envelope:

```json
{
  "schema": "control-plane-kit.operations.activity-plan-record",
  "version": 1,
  "derivation_profile": "structural-v1",
  "plan": {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": []}
}
```

Schema, version, fields and profile are closed; boolean versions and nested
envelopes are invalid. The unchanged Core codec owns the nested plan. Unknown or
malformed envelope data never retries legacy decoding. Known malformed values
become fixed `PlanDerivationError` outside the parser exception context; unrelated
failures retain their identity. No candidate values enter the error message.

This module imports only Core and the existing pure Operations transition
language. It does not import records, stores, effects, credentials or providers.
The profile records semantics, not execution authority, freshness or authenticated
history. Matching independent record/action evidence detects inconsistency; it
does not prevent coordinated rewriting of all durable evidence.

New readers understand legacy and profiled records. Old readers reject the new
envelope, including empty plans. Reader support must precede profiled writes;
old-reader rollback after those writes requires separate reviewed work. Removing
markers or rewriting historical rows is not rollback. No schema change, backfill,
reset or live rollout occurs here. Fresh service policy remains structural in
#1837; #1834 owns later graph-pair adoption.
