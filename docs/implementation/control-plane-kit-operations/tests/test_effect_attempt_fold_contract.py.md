Source: [control-plane-kit-operations/tests/test_effect_attempt_fold_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_fold_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests protect the public fold command/result language through actual
constructors and selected malformed values. They inherit the
[fold fixture](effect_attempt_fold_fixture.py.md) and add two result-building
helpers. No service is instantiated, unit of work opened, database queried or
provider invoked. "Existing" result construction is not persisted replay evidence.

The optional-loader test injects a nested ModuleNotFoundError and checks that the
same object escapes; a separately injected ImportError must escape by type. It
does not test the exact missing top-level-module-to-None branch. Presence guards
come from the fixture and fail explicitly when required bindings are unavailable.

The command-shape test requires package-root identity, the exact defining module,
dataclass/frozen metadata and the ordered six fields request_id, transition,
authority, fence, failure and outcome. Reconstructing the same fields must yield
an equal command. A command subclass must be rejected. This inspects the frozen
declaration, not attempted assignment, deep mutation or every constructor default.

The seven fixture stories span four direct outcomes and three recovery choices.
Each default command must have the expected transition; toggling failure presence
must reject, as must a STARTED transition. The actual
[fold owner](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
also relates direct outcome, derived transition and canonical failure, while the
recovery arm requires outcome=None. These tests use inherited synthetic defaults;
they do not approve a recovery decision or observe how it was obtained.

Selected nested negatives substitute subclasses of transition, identity, worker
authority, fence, failure/details and recovery decision, plus str subclasses at
fingerprint, worker, decision and failure leaves. The hostile identity implements
permissive equality. This file does not record dispatch counts, so rejection is
not proof that no hostile method ran. Some substitutions also break failure or
direct/recovery congruence: every negative is rejected, but not every row isolates
one exact-type guard from all other rejection reasons.

Eight further coordinate cases reject empty/None/bool request IDs, 513 characters,
newline, a lone surrogate, a str subclass and authority for worker-b with the
default worker-a fence. There is no positive 512-character boundary row or exhaustive
Unicode equivalence claim. The actual request validator admits exact UTF-8-encodable
text of 1..512 characters without characters below U+0020; run IDs use their separate
Core language. Scope membership and current lease freshness are not tested here.

Expected command/result failures use assertRaises with the owner error class,
exact fixed text and inherited assert_safe_error. That helper requires no cause
or context, combined str/repr at most 256 characters and absence of supplied canary
substrings. assertRaises accepts subclasses; these are not exact exception-type
assertions. Rows without canaries still check size/chain, but do not establish
universal sensitive-value redaction.

fold_result_record returns the inherited direct story's attempt for direct cases.
For recovery it constructs the base record and adds synthetic failure to the latest
event only for the failure story before rebuilding the record. The companion
fold_result_outcome_record constructs a direct outcome record or returns None for
recovery. These helpers adapt fixture data, not persisted events. Ordinary and
compensation event families are tested without executing compensation.

Both result variants must be root-identical frozen dataclasses with exactly
attempt/outcome_record fields. The outcome field is repr-hidden, no from_descriptor
method is defined directly on the variants, and the result alias equals their union.
Across both event families and all seven stories, both constructors preserve equal
attempts. STARTED records reject. The test name's "settled only" includes UNCERTAIN
among accepted fixture states; it must not be paraphrased as success or certainty.
Frozen metadata is not an assignment-attempt or immutable nested-record proof.

The failure-result test removes failure from four failure-bearing stories and
requires both variants to reject in both phases. It also admits failure-free
success, recovered success and abandonment. That method does not independently
add extra failure to every otherwise successful result; the actual owner supplies
the full presence rule. Direct records and recovery decisions have different
outcome-record requirements, which the fixture keeps distinct.

The fourteen malformed-result constructors cover outer/nested record subclasses,
hostile state or event values, missing direct outcome records, a recovery record
with a direct outcome record, and result subclasses. They use object.__new__ and
object.__setattr__ to bypass selected constructors. Some candidates violate several
requirements, including missing recovery-failure evidence, so the rejection set
is not an isolated proof of every deep validator. No dispatch spy or successful
store read/write occurs in this matrix.

The final test checks RuntimeError as the base of EffectAttemptFoldError, direct
inheritance/root identity for NotFound, Conflict and Denied, and safe rendering of
each manually constructed fixed-message error. It does not make the Python error
hierarchy unextendable or prove that arbitrary caller messages are sanitized.
There are no asserted public HTTP/MCP error projections here.

The actual owner reconstructs exact Core transitions/decisions, Operations
authority/fence/failure and outcome records to validate their relationships. The
[attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
checks state/event commitments, while the
[outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
binds direct results and observations to an attempt. Their constructor checks
remain distinct from current database truth. The separate
[atomic contract](../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py)
contains broader outcome and service-entry laws; this note credits only the nine
tests in its own source. Provider authenticity, locking, atomic writes, durable
idempotency, ambiguous outcomes and restart require other owners' evidence.

Read depth: full 568 test/helper lines and retained full 518-line fold owner,
259-line parent and relevant inherited record/outcome builders; actual selected
Core/authority/fence/failure and record validation contracts, with the preceding
atomic/guarded contract review retained. This companion was authored without
executing tests/imports, Docker, database, credentials or provider actions.
