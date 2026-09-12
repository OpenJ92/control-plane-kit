Source: [control-plane-kit-operations/tests/effect_attempt_fold_fixture.py](../../../../control-plane-kit-operations/tests/effect_attempt_fold_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This fixture assembles the public fold language and synthetic inputs for its
consuming tests. It inherits outcome, attempt and event builders from
[EffectOutcomeEvidenceFixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py).
There are no test methods or database/provider calls here. maxDiff=None allows
untruncated unittest diffs; it is not an evidence-size or redaction policy.

_load_optional imports a named module through a replaceable importer argument.
It returns None only when ModuleNotFoundError.name equals that requested name;
nested missing dependencies and other import failures escape. Both the fold
language and interpreter are loaded at module import time, and missing public
attributes are bound to None with getattr. This scaffolds explicit interface
assertions, not runtime fallback or an alternative implementation.

require_fold_language checks that eight named language/error/result symbols are
present. require_fold_service checks only the service binding is non-None.
Neither establishes callable signatures, root-export identity or behavior.
The two atomic-surface guards additionally compare exact ordered dataclass field
names: six command fields and two fields for each result variant. They do not
check types, defaults, freezing, repr policy or constructor semantics. A present
but malformed non-dataclass can raise rather than become a missing-interface
assertion. The consuming [fold contract tests](../../../../control-plane-kit-operations/tests/test_effect_attempt_fold_contract.py)
add their own nominal/value/error laws; those are not implicit fixture coverage.

FOLD_STORIES names four direct outcomes (succeeded, failed, unsupported, uncertain)
and three recovery cases (recovered-succeeded, recovered-failed, abandoned).
FAILURE_STORIES selects the three negative direct outcomes plus recovered-failed.
These are test selectors, not a parser for arbitrary provider responses or an
exhaustive declaration of the underlying outcome language.

outcome_story scans freshly constructed inherited stories for execution-<name>
and a compensation flag compared by identity. Thus this convenience path selects
execution-result stories, not the inherited provider-observation stories. It
raises StopIteration when no match exists; it does not validate or normalize the
selector. direct_outcome passes that story to the actual typed outcome builder.
direct_outcome_record takes the same name/compensation inputs, derives deterministic
observation IDs and calls the actual production observation projection with
workspace-a and the story's attempt, then constructs EffectAttemptOutcomeRecord.
It supplies no verification intent and writes no rows. Observation construction
does not establish live endpoint health or global uniqueness of generated IDs.

transition first constructs the default attempt identity. For a direct story it
derives the transition from a new direct outcome. For recovery it maps the three
names to Core resolution values and constructs decision-a with fixed all-c and
all-d fingerprints. Abandonment uses ABANDONED; the other two use RECONCILED.
The actual [Core recovery values](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
validate shape and decision/attempt/resolution congruence. These fixture decisions
do not prove that uncertain provider state was observed, resolved or approved;
no Core fold/state transition is executed by this helper. Unknown recovery names
raise from the lookup rather than producing a generic decision.

failure(marker) constructs terminal FailureEvidence with the marker interpolated
into both code and message and default empty details. The word "safe" is literal
fixture text, not a sanitizer: callers must not pass real protected values. The
actual record constructor provides its own validation, but this helper neither
redacts arbitrary markers nor asserts safe exception rendering.

authority defaults to worker-a and EXECUTION_OPERATE and delegates to
ExecutionWorkerAuthority, whose constructor normalizes scopes into a sorted unique
tuple. execution_fence defaults to the same worker and generation seven and uses
the actual [lease value](../src/control_plane_kit_operations/execution_leases.py.md).
These are declared worker capabilities and a nominal generation, not authentication,
active-claim lookup, clock expiry or proof that a current fence admits a write.

command checks the atomic command surface, prepares the direct outcome or None,
then builds request-a, transition, authority, fence and failure defaults. For the
direct arm, transition(story) constructs another equal-default outcome rather
than reusing the first object; production congruence is value-based. The recovery
arm has failure only for recovered-failed and no direct outcome. Keyword changes
overwrite any defaults before the actual FoldEffectAttempt constructor runs;
dependent defaults are not recomputed after those overrides. This deliberately
lets tests propose inconsistent combinations and observe owner rejection.

The actual [fold language](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
revalidates command authority/fence and transition/failure/outcome relationships.
Its result constructors bind direct outcome records to non-STARTED attempts and
keep recovery outcome records absent. The fixture exports those constructors and
the service symbol; it does not instantiate the service, open a unit of work,
call execute, commit, replay or invoke an interpreter. The separate
[atomic fixture](atomic_effect_attempt_fold_fixture.py.md) specializes the parent
for full OutcomeStory objects and recovery result construction. No provider
recovery, compensation or mutation authority follows from either fixture.

Read depth: full 259-line source/all helpers; retained selected outcome-story,
observation and attempt-record construction paths; actual fold command/result
validation, Core decision/transition definitions, authority/fence/failure values
and the service entry; selected consuming import-guard/command/result assertions.
This is not a full review of the inherited outcome fixture, interpreter or suite.
No tests/imports, database, Docker, credential or provider actions were executed.
