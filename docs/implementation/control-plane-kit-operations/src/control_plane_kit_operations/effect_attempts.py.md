Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 294-line Operations module defines the commitment between one Core
EffectAttemptState and its original/latest activity events. Its three public
exports are EffectAttemptEventEvidence, effect_attempt_state_fingerprint and
EffectAttemptRecord, also re-exported by the Operations root. It uses Core
operation values, generic Operations records and standard-library hashing/JSON;
it does not allocate IDs, execute transitions, authenticate a claim, access a
store or invoke a runtime provider.

EffectAttemptEventEvidence is a frozen two-field dataclass containing attempt
and state_fingerprint. Admission requires an exact int from one through
2,147,483,647 and an exact str matching 64 lowercase hexadecimal characters.
Booleans and scalar subclasses therefore fail these checks. Rejection raises
OperationsRecordError with the fixed event-evidence-invalid message. descriptor
returns a fresh two-key dictionary; there is no evidence decoder in this module.
The constructor checks its fields, not an explicit type(self) restriction on the
evidence dataclass itself.

effect_attempt_state_fingerprint accepts only an exact EffectAttemptState and
hashes the following representation:

```python
json.dumps(state.descriptor(), sort_keys=True,
           separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

The result is a plain SHA-256 hexadecimal digest, with no domain prefix or
RFC 8785 step. The
[Core state descriptor](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
includes identity, request fingerprint, fence, status, outcome fingerprint,
prior attempt and recovery decision. Event IDs, ordinals, times and failure
payloads are not part of this state digest. The function's exact top-level check
does not reconstruct or fully revalidate every nested field, and serialization
exceptions are not universally caught or normalized. This digest is a commitment
to data, not authenticated evidence that a provider performed an effect.

EffectAttemptRecord is a frozen three-field value: state, original_start_event
and latest_transition_event. Its constructor calls _record_is_valid and raises
the fixed record-invalid OperationsRecordError when that predicate returns false.
The constructor has no explicit exact-self-type test; the exactness checks apply
to its contained state/events and nested values. Consumers such as stores and
fold-result constructors can impose an exact EffectAttemptRecord type separately.

Validation first checks state shape, then original event shape and latest event
shape with short-circuit boolean guards. _state_is_exact requires exact state,
identity, fence and enum types, exact text/integer field types, and exact optional
prior/recovery substructure. Identity includes an exact RunId and string value;
recovery includes exact decision text, identity, resolution and fingerprint text.
These are shape checks rather than re-execution of every nested constructor's
bounds, fingerprint syntax or semantic relationship rules.

_event_is_exact requires exact ActivityEventRecord, an exact string event ID
that can encode as UTF-8, exact run/time strings, exact integer ordinal in the
same PostgreSQL range, exact event kind, optional exact activity text, exact
BoundedEvidence and optional exact FailureEvidence. Lease-recovery evidence must
be None. The evidence shape helper checks exact canonical_json string type;
the failure helper checks exact category/code/message/details shapes. They do
not independently reparse existing evidence JSON or reconstruct FailureEvidence.

The UTF-8 helper only checks exact string type and successful encoding. Ordinary
nonempty/control-character/text bounds remain responsibilities of the original
[ActivityEventRecord and evidence constructors](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
Likewise, positive ordinals are admitted there, while this module adds the
2,147,483,647 upper bound. The word exact does not make these helpers a complete
admission boundary for arbitrary objects forged without their constructors.

After shape checks, the original event must be STEP_STARTED or
STEP_COMPENSATION_STARTED. That choice determines the event phase. The validator
constructs a STARTED state retaining current identity, request fingerprint, fence
and prior attempt but clearing outcome and recovery decision. The original event
must commit to this reconstructed start, not to the current terminal state.
This reconstruction applies Core state admission to that start value, but does
not reconstruct the complete final state or every nested identity/fence object.

_event_commits_to constructs exactly this bounded evidence envelope:

```text
{"effect_attempt": {"attempt": <attempt number>,
                    "state_fingerprint": <state digest>}}
```

It compares expected kind, run, activity and the entire BoundedEvidence value.
An extra field, absent field, foreign coordinate, wrong attempt or wrong digest
fails equality even if some expected fields match. BoundedEvidence supplies
canonical JSON and its own size/content restrictions. The commitment helper
does not itself write an event or query persisted history.

_EVENT_KIND_BY_STATE has sixteen entries: eight configurations mirrored for
ordinary and compensation phases. Without recovery, STARTED, SUCCEEDED, FAILED,
UNSUPPORTED and UNCERTAIN select their corresponding step event kinds. With
recovery, SUCCEEDED and FAILED select uncertainty-resolved kinds and ABANDONED
selects uncertainty-abandoned. Unsupported phase/status/recovery-presence
combinations have no entry and are rejected. This selects the current event
representation; it does not apply the Core transition law or prove a recovery
decision was authorized against an earlier uncertain state.

For STARTED, latest must equal original as a value. A separate but equal event
object is not excluded by this condition. For every non-started record, event
IDs must differ, latest ordinal must be strictly greater and latest must commit
to the current state with the selected kind. Timestamps need not be chronological
or distinct. They are checked here as exact strings, not parsed or compared.
The validator does not inspect intermediate events or prove a gap-free history.

Failure presence is not derived from final status here. Generic event admission
restricts which kinds may carry a failure, and this module checks the exact
failure shape when one is present, but a failed/unsupported/uncertain record can
have failure=None. The stricter
[fold-result contract](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
requires appropriate failure presence and outcome relationships. An admitted
EffectAttemptRecord alone is not a complete folded outcome, active authorization
or proof of a successful runtime action.

Fixed errors apply to the explicit rejection paths. _record_is_valid may call
Core constructors, evidence construction or JSON/UTF-8 hashing without a blanket
exception wrapper, so errors from those operations can propagate instead of the
fixed record message. Nested exact-type guards reject the selected subclasses
covered by tests but do not establish universal hostile-dispatch avoidance or
safe rendering of arbitrary forged values. Default dataclass repr also has no
special redaction policy in this module; callers must preserve the bounded-data
contracts rather than treating a record repr as a secret scrubber.

The selected
[start interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
plans a Core started state, builds this evidence and one event, then constructs
EffectAttemptRecord(state, event, event). The selected
[PostgreSQL adapter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
reconstructs typed state values from row columns, loads referenced events, checks
their stored coordinates and calls this constructor. Its row-error normalization,
comparison-and-set rules and transaction ownership are adapter responsibilities.
The present module neither commits nor rolls back those operations and has no
retry, cleanup or retained-history policy of its own.

The five fully read
[evidence tests](../../tests/test_effect_attempt_evidence_contract.py.md)
protect root identity/frozen field shape, selected current/historical hashes,
bounded evidence cases, one state subclass rejection and selected AST/inventory
constraints. Their historical commitment is raw evidence, not a valid current
typed intent. Their AST check restricts listed import substrings rather than
proving all possible transitive effects absent.

The nine fully read
[record tests](../../tests/test_effect_attempt_record_contract.py.md)
cover sixteen story/phase constructions, retry/reversed/equal time acceptance,
nominal and nested subclasses, ordinal/UTF-8 bounds, original/latest commitment
faults, kind/phase mismatch and STARTED equality. They use constructor-admitted
substitutions rather than raw exact-type forgeries and do not instrument hostile
dispatch or execute PostgreSQL/restart behavior. Local digest expectations share
the Core descriptor and JSON formula; they are not independent transition oracles.
The selected architecture inventory assigns this module to "operation", with
the three canonical exports and no optional external dependencies.

Read depth: all 294 source lines and helpers were read, together with the complete
378-line record fixture, 213-line evidence test and 519-line record test. Selected
actual Core state, generic records/evidence, root exports, fold-result, start
planning, PostgreSQL reconstruction and inventory contracts were cross-checked;
this is not full review of those adjacent owners. Validation for this companion
used local links, whitespace and frozen-source comparison only. No application
imports, tests, database/provider calls, credential access, source/inventory
edits or publication were performed.
