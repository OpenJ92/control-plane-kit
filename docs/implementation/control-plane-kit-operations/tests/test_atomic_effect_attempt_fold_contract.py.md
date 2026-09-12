Source: [control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py](../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These twelve tests protect the direct-outcome/recovery-without-outcome command
and result forms, selected hostile inputs, preflight rejection and declared
architecture surfaces. They use typed synthetic values from the
[atomic fixture](atomic_effect_attempt_fold_fixture.py.md) and its
[parent](effect_attempt_fold_fixture.py.md). No test here enters a working unit
of work or persists an atomic aggregate. The word atomic names the contract being
protected; database atomicity and replay execution need separate evidence.

The predecessor-law test requires twenty direct stories: four execution-result
variants and six provider-observation variants, each with ordinary and compensation
event families. For each, the production outcome-to-transition function must
match the story's explicit transition kind, identity and fingerprint, and its
failure projection must match the story's latest event failure. The constructed
outcome record must retain the story attempt and match separately constructed
expected endpoint observations. Six recovery records, covering three resolutions
and both event families, must reconstruct as equal EffectAttemptRecord values.
These are constructor consistency witnesses, not executed predecessor transitions.

The [outcome fixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py)
builds the story states/events and fingerprints using production value types and
fingerprint functions. Its expected observation helper explicitly maps endpoint
values to UNKNOWN, FRESH, transport-probe records at the latest event time; the
atomic fixture obtains actual observations through the production bridge. That
comparison preserves a meaningful mapping assertion while sharing admitted
values, identifiers and some production helpers. The twenty stories do not
include every possible outcome payload or the separate verification-intent path.
The atomic fixture supplies no intent record to the observation bridge.

Command surface checks require the exact field order request_id, transition,
authority, fence, failure, outcome. FoldEffectAttempt must be a frozen dataclass,
the root export must be the same class and the outcome field must have repr=False.
One ordinary execution-succeeded command keeps its outcome by equality and omits
three selected strings from combined str/repr. Its endpoint URL is present in the
hidden outcome, but the failed-provider and observer canaries belong to other
stories; their absence here is not exhaustive sensitive-payload coverage. Frozen
metadata is inspected, rather than an attempted mutation being rejected by a test.

Across all twenty direct stories, commands retain an outcome, derive exactly its
transition and failure and carry no recovery decision. The four direct negative
constructors remove the outcome, substitute the failed story's outcome, substitute
an UNCERTAIN transition or add synthetic failure evidence to the succeeded story.
Each must produce the fixed invalid-command error with safe rendering. These are
selected incongruent inputs, not an exhaustive proof of the test title's iff:
the UNCERTAIN transition also lacks its required failure, and the extra failure
on SUCCEEDED already violates failure presence. Those cases can reject before
the final outcome-congruence comparison. Substituting the failed outcome disagrees
in transition kind/fingerprint and canonical failure together. Removing a direct
outcome isolates its required presence.

Recovery command tests construct recovered-succeeded, recovered-failed and
abandoned commands with absent outcomes and present recovery decisions, then
reject an otherwise lawful recovered-succeeded command carrying a direct outcome.
This isolates forbidden outcome presence for that recovery form; it does not
exhaust every malformed decision or failure combination. The helpers fabricate
decision IDs, fingerprints, worker scope and generation. They do not obtain a
live recovery decision or authenticate a worker.

The hostile-outcome test creates ExecutionEffectOutcome and ObservedEffectOutcome
subclasses without calling their constructors, copies the legitimate fields and
overrides outcome_fingerprint to record access and raise a canary-bearing error.
It also forges exact outer types with a malformed nested result kind or an object
in place of an observation. All four commands must reject categorically, omit
the supplied canaries and leave the shared fingerprint-dispatch list empty.
The property spies establish non-dispatch for those two hostile properties. The
exact-type forgeries establish rejection of selected nested invalid values; they
do not instrument every possible property, equality operation or nested method.
forge_exact uses object.__new__ and object.__setattr__, deliberately bypassing
normal dataclass admission before the production command validates its input.

Result surface checks require precisely attempt and outcome_record, a union of
NewlyFolded | ExistingFold, frozen metadata on both variants and repr=False on
outcome_record. Both constructors retain the same supplied outcome-record object;
the ordinary succeeded result rendering omits the selected provider string and
endpoint URL. The acceptance matrix constructs both variants for twenty direct
stories and six recovery worlds: forty direct and twelve recovery values. Direct
results compare attempt and outcome record with fixture expectations; recovery
results assert absent outcome records. Calling ExistingFold a replay result does
not execute replay, inspect stored history or prove the caller's authority.

Ten negative result constructors exercise both variants with missing direct
records, a subclassed attempt, a forged inconsistent outcome record, a recovery
attempt plus a direct record and subclassed result constructors. They require
the fixed invalid-result error and inherited safe-error checks. The forged record
combines a failed outcome with the succeeded attempt and its observations, so it
contains multiple contradictions. Pairing a recovery attempt with an unrelated
direct record likewise violates both arm presence and record correspondence.
The copied hostile attempt and result subclasses have no dispatch spies. Their
rejection protects selected exact-type boundaries, without independently proving
every deep-validation check or the ordering of every result guard.

The actual [fold owner](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
requires exact command, transition, authority, fence and failure structures and
reconstructs their admitted values. It reconstructs exact execution/observed
outcomes before comparing their canonical transition and failure; recovery
requires the original outcome field to be None. Result constructors reject
subclasses, reconstruct the exact attempt, reject STARTED or inconsistent failure
presence, require no outcome record for recovery and reconstruct the direct
outcome record before comparing its attempt. These paths explain the selected
tests. The file's guarded command/runtime-authority validators are not exercised
by merely listing GuardedObservedEffectFold among expected exports here.

Service preflight has explicit boundary witnesses. forge_command copies the
valid command fields without admission, then makes either a direct command with
no outcome or a recovery command with one. Each service call must give the fixed
invalid-command error and leave FailIfUnitOfWork.calls at zero. Valid direct and
recovery commands must instead call that factory exactly once and propagate its
identical stored AssertionError. A structurally valid direct command with empty
scopes must raise EffectAttemptFoldDenied without calling the factory; this
part checks category and call count, not the exact denial message or redaction.
No usable unit of work, store or connection is returned by the spy.

The actual [interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
revalidates execute's command, then checks execution:operate scope and fence
representability before invoking the unit-of-work factory. This accounts for
the preflight observations. The test supplies a constant ID lambda without
counting it and does not exercise successful persistence, current-claim checks,
guarded observation dispatch, transaction rollback or provider activity.

The architecture test imports the interpreter to locate its source, reads that
file as UTF-8 and passes it to the shared analyzer with explicit path/module
coordinates. Two exact policies require no findings for the authored import and
call tuples. Imports include module, symbol and alias occurrences. Expected calls
include repeated targets and one UnresolvedCallTarget, so the policy is neither
a set of names nor a requirement that every call be resolved. It covers the
whole interpreter source, including guarded paths not behaviorally tested here.

The inspected architecture-testing checkout at
7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef matches the
[package harness](../test.sh.md) pin. Its analyzer uses the executing Python's AST
grammar and lexical alias/name facts. The evaluator compares sorted occurrence
projections, preserving multiplicity while discarding source location/order for
surface equality. Thus a changed lexical surface can fail this check, but the
same surface does not prove execution reachability, runtime call count, write
order, transaction safety or absence of indirect provider effects. This test
does not mutate source to test policy sensitivity or validate every dependency's
implementation. No analyzer or imported application code was executed for this
documentation review.

The publication-surface test requires five public fold names to be included in
the package root's __all__, and the private guarded validator to be absent both
there and as a root attribute. Apart from the separate FoldEffectAttempt identity
assertion, it does not compare every root binding with its owner. It reads
package-module-inventory.json from the repository default or the
CPK_PACKAGE_MODULE_INVENTORY override, selects the two fold modules and checks
their dependency/export lists and required protecting-test paths. Most checks
use sets or inclusion; the interpreter export is an exact one-element list.
Dictionary selection can collapse duplicate module rows, and membership of a
test path is not proof that that test exists, runs or passes. This is a focused
declaration check, not a complete inventory or observed package-graph audit.

Invalid command/result helpers require their exact categorical messages and
the inherited [record-fixture](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
error law: no cause/context, combined str/repr at most 256 characters and no
supplied canaries. Representation hiding and selected no-dispatch/preflight
checks protect the modeled boundaries. They do not inspect logs, serialize all
protected evidence, authenticate real credentials or establish durable history.
The file has no database setup, provider cleanup, retry loop or restart behavior.

Read depth: all 776 source lines, twelve tests, local helpers and complete exact
policy tuples; full 90-line atomic fixture, 259-line parent and 518-line fold
owner; selected outcome-story/projection and record/error construction helpers,
interpreter preflight, harness pin and actual shared AST/policy projection and
evaluation paths. No tests, application imports, database connections, source or
dependency changes, Docker, credentials or provider actions were executed while
authoring this companion.
