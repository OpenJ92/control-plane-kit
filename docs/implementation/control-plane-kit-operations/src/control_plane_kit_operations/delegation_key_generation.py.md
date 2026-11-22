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

`admit_generated_in_unit_of_work` joins an existing active caller transaction and
never requests commit. The existing `admit_generated` method validates first,
opens its normal UoW, invokes that shared fold, and requests commit. The typed
attempt owner uses the same fold to commit reference, key and admitted attempt
together. A retained public receipt lives in a prior transaction and survives a
failed admission. Core #2025 still owns complete public generation/status/
reconciliation, with provider I/O outside transactions and separate activation.

The shared fold takes the purpose lifecycle lock before either registration.
The existing node-control source inspection follows that shared owner after the
extraction, while retaining checks that the legacy wrapper opens its UoW,
delegates, and requests commit afterward. Behavioral fold and rollback tests
remain the evidence for transaction effects.
