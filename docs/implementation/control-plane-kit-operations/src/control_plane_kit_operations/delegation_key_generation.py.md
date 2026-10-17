Source: [delegation_key_generation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_key_generation.py).
Maintain this companion alongside its source.

Generation support is independent from persistable key purposes. The four old
purposes retain existing probe-custody preparation and evidence folding. The
public generation grant constructor, service preparation and generated-evidence
admission all enforce this explicit domain with a nominal enum check before
membership. Both health purposes receive the fixed unsupported-purpose error.

Preparation refuses before opening a UOW or yielding a provider-capable grant.
Admission checks before receipt/candidate construction or durable writes, closing
the alternate store-registration path. A caller cannot construct a normal
health-under-probe grant directly. These are refusal boundaries, not health
generation implementation. Provider-local adoption in Secrets #29 does not lift
them automatically; positive Operations generation needs its own reviewed change.

Existing permission, evidence congruence, idempotency and atomic reference/key
fold laws remain. Provider I/O stays between transactions at the interpreter
boundary. No new provider effects or secret-bearing records are introduced.
`test_delegation_key_generation.py` covers health preparation and direct grant
refusal alongside the existing exact-fold and rollback tests. The additional
fold-entry domain check is defense in depth, not a claim that its deeper body
was independently exercised by the health target-red cases.
