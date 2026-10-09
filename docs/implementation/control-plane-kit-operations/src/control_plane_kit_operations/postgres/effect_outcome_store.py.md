Source: [effect_outcome_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py).
Maintain this companion alongside its source.


B1 / #1923 configuration reads use the command's shared pretransport budget for
the complete outcome row, full bounded preimage, and ordered observation
membership plus a completeness sentinel. Joined membership/observation reads
charge both relational identities. Existing outcome, event and observation
validators remain authoritative; no alternate state machine or cached mutable
authority is introduced.

B2 adds private `_configuration_success`: bounded point reads retain the complete
original result, all ordinary result observations, and original/direct event
coordinates. The existing pure outcome reconstruction and correlation law are
shared with the full record decoder. The private proof does not manufacture an
`EffectAttemptOutcomeRecord` with empty memberships or scan observation projection
memberships; the generic reader and current-row verifier retain all their existing
membership obligations. Only direct execution-result SUCCEEDED with exact source
fingerprint, effect, request/workspace, identity and event correlation qualifies.
C/D completion profiles and protective-claim disposition are not implied. This
source checkpoint passed concrete review and the focused outcome behavior and
acceptance tests. Two exact declaration failures were then corrected and passed
their own selected gate with compilation and clean import; PR #1926 retains the
separate run results. Public current-use and reuse acceptance remain pending.
