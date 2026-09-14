Source: [control-plane-kit-core/tests/test_effect_recovery_contract.py](../../../../control-plane-kit-core/tests/test_effect_recovery_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Effect recovery test evidence

Sixteen pure tests cover the [attempt transition language](../src/control_plane_kit_core/operations/recovery.py.md), its public exports and selected event vocabulary. Fixtures supply synthetic IDs/fingerprints and construct states by folding; no PostgreSQL connection, worker lease, approved retry or provider result is involved.

Admission cases include positive/native integer ceilings, boolean and hostile integer-subclass rejection, 256-character Unicode identifiers, NUL/surrogate rejection and hostile string overrides. The custom assertion checks exact fixed messages, no cause/context and absence of supplied canaries for these selected cases. It does not establish universal safe errors, especially for untested malformed enum decoding. Native PostgreSQL-domain terminology describes admitted values rather than a live database round-trip.

Transition cases cover exact prior-attempt lineage, all four direct results, refusal of ordinary success/failure after uncertainty, matching reconciliation evidence, wrong uncertain fingerprint, explicit abandonment, unequal worker/generation fences, terminal idempotency and conflicting duplicate results. Successful reconciliation retains its decision, returns the same object on exact replay and round-trips through the state codec. These tests do not authorize a new attempt or demonstrate stored lease/CAS behavior.

The phase/event test enumerates 16 forward/compensation combinations and checks that ActivityEventKind and ActivityJournalEventKind accept their event strings. It also rejects contradictory reconciliation/abandonment resolutions. This is vocabulary coverage; it does not call the actual Operations event mapper or execute either phase. Export assertions check ten names on both facades, not every public symbol or full import isolation.

Descriptor tests round-trip representative values and reject an extra secret field; other cases reject missing/contradictory transition material, malformed fingerprints and an overlong decision ID. The selected examples are not an exhaustive cross-product of every state, codec and invalid field. Full 750-line test/full 679-line owner and identity helpers read, with selected actual Operations consumer boundaries. No executable validation was performed.
