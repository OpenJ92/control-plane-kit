Source: [delegation_key_generation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_key_generation.py).
Maintain this companion alongside its source.

Generation support is explicit and independent from a later public route's
allowlist. The four legacy purposes retain probe-custody preparation and evidence
folding. Workload health and gateway health transit use the exact intent returned
by Core `health_signing_intent_for`. One internal mapping supplies preparation,
public grant-constructor validation and the fold-entry purpose guard, with a
nominal enum check before membership.

Preparation requires generation permission and an active provider whose admitted
reference prefix and intents permit the exact request. A requirement for one
health intent cannot be satisfied by probe or the other health intent. Direct
grant construction enforces the same pairing. The grant remains reference-only;
this module does not invoke a provider or activate a key.

Existing permission, evidence congruence, idempotency and atomic reference/key
fold laws remain. Provider I/O stays between transactions at the interpreter
boundary. No new provider effects or secret-bearing records are introduced.
`test_delegation_key_generation.py` applies the same exact-fold, replay, permission,
evidence mismatch and rollback laws to probe and both health families using
providers admitted only for the corresponding intent. It also preserves the
legacy mappings and refuses direct cross-intent relabeling. The focused red
failed at the old health-purpose restriction before health admission assertions
were reached; green evidence must exercise those later assertions.

Core #2024 owns typed durable attempts and the transaction-participating admission
seam; #2025 owns the complete public generation/status/reconciliation service.
This leaf adds no table, route, automatic retry, provider I/O or live-effect
authority. Existing public types and the single-UoW reference/key fold remain.
