Source: [control-plane-kit-operations/tests/test_execution_lease_recovery_result.py](../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_result.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests protect the pure
[ExecutionLeaseRecoveryResult](../src/control_plane_kit_operations/execution_lease_recovery.py.md)
contract using constructed request/run/event/action records. The import guard
masks only absence of the exact owner module; missing nested dependencies escape.
No database, lease clock, persisted action lookup, authenticated authority or
recovery interpreter is involved.

The fixture builds all four lease-recovery forms. Renewal keeps worker-a while
advancing generation seven to eight; takeover switches to worker-b at eight;
abandonment has no replacement. The request is CLAIMED with that replacement fence
or ABANDONED without a claim. The retained run is CLAIMED for active renewal and
FAILED, started and unsettled for other forms. Decision/consequence events belong
to run-a at ordinals four/five, with the corresponding recovery/consequence kinds.
Their times and action creation use observed; other timestamps are placeholders,
not observations of a real lease or canonical database clock values.

The action is RECORD_RECOVERY_DECISION in session-a with ten common payload
coordinates and duration 600 for non-abandonment. It carries a fabricated a*64
fingerprint and an idempotency key. No originating recovery command is supplied,
so these tests cannot demonstrate command-to-action fingerprint, actor, duration
or authority correspondence. Actual
[record constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
enforce local timing, evidence and event laws before the result is constructed.

The shape/descriptor test requires exact root-export identity, frozen dataclass
status and the six result field names. It compares the complete descriptor for
all four variants, including compact event coordinates and recovery evidence.
Selected authority-reference/scope/lease-time strings must be absent from descriptor
repr. This does not inspect full result repr or an arbitrary serializer; some
canaries, including the authority reference, are not fields of the fixture result
in the first place. Frozen wrapper checks do not establish deep Mapping immutability.

The cross-record test supplies 23 selected mutations spanning request/run ownership,
foreign decision run, event IDs/ordinals/times, consequence kind/evidence, action
session/kind/time/idempotency/fingerprint and a non-bool replay flag. Each expects
OperationsRecordError. Most retain the original action payload while changing
another record, so rejection can arise from payload correspondence as well as an
event/lineage check. The duplicate-event-ID mutation likewise does not update its
action coordinate; it is not isolated proof of the distinct-ID validator branch.
Action actor is not mutated here.

For every variant, the payload test removes and changes each of ten common
coordinates: request, plan, run, decision ID/kind/ordinal, consequence ID/kind/
ordinal and recovery. It also rejects an extra key. For each non-abandon form it
rejects missing duration, bool, zero, 3601 and string 600; abandonment rejects a
duration key at all. These cover 80 common-coordinate cases, four extras and 16
duration cases. They do not positively test both valid duration endpoints or
reject every possible alternate in-range value.

The actual result accepts an in-range duration from its payload, compares complete
Python dictionaries and validates fingerprint shape. These tests do not define
byte-canonical encoding, exhaust numeric/bool equality in every coordinate or
derive lease expiry from action duration. Binding duration to the submitted
command and claim times belongs to the
[interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py).
The pure result has no original command with which to make that comparison.

The claim/status matrix rejects four selected request states: abandonment in a
renewal result, wrong renewal generation, wrong takeover worker and queued state
for abandonment. It rejects FAILED in a direct active-renewal result and CLAIMED
in each other direct recovery form. These combinations protect current record
correspondence; they do not observe whether a represented lease has actually
expired or validate a journal proving why the retained run failed.

The active-replay test lists all ten current ActivityRunStatus values and asserts
its tuple equals the enum tuple. It constructs timing-compatible run records and admits
each with replayed=True; every non-CLAIMED status must fail in a direct active-renewal
result. For expired renewal, takeover and abandonment, replay preserves FAILED and
rejects a selected RUNNING replacement. It does not exhaust every non-FAILED status
for those three forms or execute any lifecycle transitions. The actual record
timing contract allows the tested cancelled form to carry no started_at.

The final test constructs valid shared retry-as-new-run evidence with an unchanged
fence and puts it on the decision event of a recovery result. It requires exactly
recovery result decision kind is invalid, with a bounded chain-free error. This
distinguishes the broader shared evidence language from the four lease-recovery
result variants and prevents the missing consequence mapping from surfacing as an
unclassified lookup error. It is not a retry interpreter or new-run execution test.

assert_result_rejected builds the actual result from the altered records;
assert_safe_error requires no cause/context and combined str/repr length at most
512. This result-test helper does not accept canaries or assert arbitrary text is
absent. The source requires exact types for five record fields and exact bool,
but these six tests do not supply a full hostile-wrapper matrix. The source does
not reject the result's own subclasses in its type helper; this file does not
assert such rejection or recursive exact typing of nested record fields.

The [contract tests](test_execution_lease_recovery_contract.py.md) separately cover
command shape/fingerprints, authority normalization and imported fence/event laws.
Neither file proves actual approval, active operator authority, row locking,
transaction atomicity, historical replay reconstruction or safety of repeating
an external effect. Constructed coherent records are evidence of value admission,
not proof that the represented operation occurred.

Read depth: full 513-line source, all six methods and fixtures/helpers, full
442-line owner and 688-line neighboring contract tests; actual evidence, run-timing,
fence/key contracts and selected interpreter command/time binding were inspected.
No source/pin changes, executable tests, database setup, credentials/private-key
access, provider/runtime actions or publication occurred. Documentation adds no
security surface and makes no claim that this suite ran or passed during authoring.
