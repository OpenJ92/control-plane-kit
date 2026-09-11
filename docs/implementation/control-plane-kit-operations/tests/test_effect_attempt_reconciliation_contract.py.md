Source: [control-plane-kit-operations/tests/test_effect_attempt_reconciliation_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_reconciliation_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These ten tests cover reconciliation command admission/publication, declared
observer/service interfaces, synthetic observation projections and selected
realization-context event admission. They instantiate values and inspect source
metadata, signatures, type hints and inventory JSON. They do not instantiate the
reconciliation service, call an observer, open a UoW or prove durable reconciliation.
The [reconciliation fixture](runtime_effect_reconciliation_fixture.py.md) supplies
captured optional owner symbols and the nominal four-field command; a separate
outcome fixture supplies observation stories.

The import test injects a ModuleNotFoundError naming a nested dependency and
requires that identical error object to escape _load_optional. A separate injected
ImportError must escape as ImportError, without an identity assertion. It does not
directly exercise successful import or exact-owner absence. The actual helper
returns None only when error.name equals the requested module; missing transitive
dependencies and partial imports are not converted into missing-publication credit.

The command test requires root identity, the language module name, dataclass and
frozen metadata, ordered request_id/identity/authority/fence fields and equality
with direct construction of the same coordinates. It rejects a command subclass
with the exact invalid-command message and safe-error helper. Checking that
from_descriptor is absent from the class's own __dict__ does not prove no inherited
or external decoder exists. Frozen metadata is not an attempted mutation test,
and equality here does not enumerate all annotations, defaults or public members.

The malformed-command matrix has 26 rows. It includes empty/non-string/513-ASCII-
character/control/surrogate/subclass request IDs; subclass identity, authority and
fence; shallow exact-type forgeries with malformed or hostile nested RunId,
activity, attempt, worker, scopes or generation; and a worker/fence mismatch.
Every row must raise InvalidOperationCommand with the same fixed message, pass
safe-error checks and leave three independently cleared dispatch lists empty.
The subtype cases and authority-worker mismatch can violate more than one guard;
the matrix is not one isolated witness for every predicate in the constructor.

HostileText instruments __class__ access, length, iteration and encode;
HostileInt instruments index and less-than/less-or-equal comparisons;
HostileTuple instruments iteration, length and indexing. These checks prohibit
those selected operations on the supplied hostile values, not every possible
dynamic operation on all nested objects. The plain identity/authority/fence
subclasses have no analogous dispatch instrumentation. forge_exact deliberately
uses object allocation/assignment to bypass their normal constructors.

The actual [138-line language owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation.py)
rejects non-exact command/nested types, bounds text by UTF-8 bytes, rejects control
characters and reconstructs identity, authority and fence before equality checks.
Authority and fence worker IDs must agree. The text limit is 512 bytes, not merely
characters; this test's 513-character case is ASCII and supplies no multibyte
boundary pair or exact-512 positive. It also does not prove execution:operate is
required by the value: that check belongs to the service. Shape admission cannot
establish current claim authority, persisted intent or lease freshness.

The error-family test requires the base's sole RuntimeError parent and the three
named NotFound/Conflict/Denied classes' sole reconciliation-error parent, plus
root identity. It constructs each leaf with fixed categorical error and applies
safe-error checks with synthetic secret/address/provider canaries. Those canaries
were never supplied to an error-producing service path; this is safe rendering
of an already fixed message, not adversarial service redaction. Nor does checking
these three classes seal Python subclassing or prove no additional error class
exists. The test title's closed sum is stronger than that literal enumeration.

The protocol test requires root/module identity, exact Protocol base and protocol
flag, observe's self/request/authority parameter names, and resolved request,
optional registration and six-variant result annotations. It similarly checks
the service's module/root identity, constructor parameter names
unit_of_work_factory/observer/fold_service, execute's self/command names and
ReconcileEffectAttempt-to-EffectAttemptFoldResult annotations. Existing fold-result
root identity is preserved. Parameter names and hints do not prove parameter
kinds/defaults, provider read-only behavior, actual return admission or execution
outside transactions. No observer instance is exercised by this method.

The projection matrix requires exactly twelve provider-observation stories,
ordinary and compensation phases, and the six expected variant class names.
For each it checks exact ObservedEffectOutcome, original-event/effect-ID and
attempt/request-fingerprint agreement, correct start-event family, full projected
transition and canonical failure. The
[outcome fixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py)
builds synthetic attempt/event worlds and uses production fingerprint and value
constructors; expected transitions/failures share that material. These are pure
projection-consistency witnesses, not provider observations, persisted events or
an independently implemented fingerprint oracle. Variant coverage uses names,
not a direct exact-type assertion on each nested observation value.

Four foreign-input cases change either effect_id or request_fingerprint for an
observed-success story in each phase. ObservedEffectOutcome construction and
transition/failure projection must remain lawful. The final mismatch check is an
OR against the original event ID and the fixture-level REQUEST_FINGERPRINT constant,
not two separate comparisons against the selected attempt's current coordinates.
Projection return values are not asserted in this method. It demonstrates that
individually representable foreign observations exist; it does not call the
reconciler or prove that it rejects their joins to durable intent.

The two context tests use _context from the
[runtime translation tests](../../../../control-plane-kit-operations/tests/test_runtime_effect_translation.py),
which constructs a pinned synthetic StartNode realization with request/run/plan,
graph/product and worker/fence values. _event_with_kind copies all dataclass
fields into an uninitialized exact ActivityEventRecord, replacing only kind, so
these rows bypass the event constructor and target context admission itself.
Both start kinds must be accepted and the translated runtime request's effect_id
must equal that event's ID. This does not exercise a separate compensation plan
or assert the whole translated request.

Every other currently iterable ActivityEventKind must reject with the one fixed
start-kind message and no cause/context. Two non-enum HostileKind values either
raise or claim equality; both must reject without __eq__ dispatch. The actual
[context owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
uses identity guards for STEP_STARTED and STEP_COMPENSATION_STARTED, then checks
activity correspondence. These tests change kind alone; they do not cover every
other context/graph/authority invariant or transaction. The
[translation entry](../src/control_plane_kit_operations/runtime_effects.py.md)
preserves the intent-event ID when constructing the pure runtime request and
supplies an empty resolution-grant tuple; it does not execute that request.

The final inventory test requires seven names to be present in root __all__,
not equality with the entire root export set. It selects the two owner rows by
module name into a dictionary, checks operation ownership, their declared export
and dependency sets, protecting-test names and interpreter motivation text.
Most comparisons use sets, so order and duplicates are discarded; duplicate
module rows collapse in the dictionary. The interpreter export alone is an exact
one-element list. No observed import graph is computed here, and listed protecting
tests are not executed or checked for behavioral completeness. The JSON path may
be overridden by CPK_PACKAGE_MODULE_INVENTORY. The only/closed wording in the
test title does not establish global inventory uniqueness or exclusive exports.

The inherited error helper requires empty cause/context, combined str/repr at most
512 characters and exclusion of each nonempty supplied canary. Error tests and
the constructor matrix use it; event-kind tests check their own narrower message/
cause/context laws. There is no general payload/log/storage redaction audit here.
Database authority, secrets, observer orchestration, replay and guarded folding
remain responsibilities of their separately tested owners.

Read depth: all 675 source lines, ten methods, every hostile/forgery/event helper;
full 138-line language and 109-line fixture; selected inherited identity/authority/
fence/error helpers, actual outcome story/projection builders, _context, context
event admission, translation entry and service scope check. The full coordinator,
reconciliation interpreter and all consumer suites were not reviewed for this note.
No imports, tests, databases, Docker, providers, credentials, source or dependency
changes were executed while authoring it.
