Source: [control-plane-kit-operations/tests/test_effect_attempt_fold_interpreter_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_fold_interpreter_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests protect service publication, pre-transaction command/scope/fence
admission and selected package-ownership declarations. They inherit the
[fold fixture](effect_attempt_fold_fixture.py.md), construct the real service and
use a factory that deliberately raises before producing any unit of work. They
do not open a database, execute a provider, commit a fold or prove durable replay.

FailIfUnitOfWork owns one AssertionError object and increments calls before raising
it. The local service helper supplies that factory and a constant unused-event-id
factory. bypass reconstructs six command fields with object.__new__ and
object.__setattr__, allowing malformed candidates past constructor admission.
These are boundary probes, not fake stores or an alternative fold implementation.

The publication test checks nine named bindings in operations_root.__all__ and
their object identity, the service's defining module, constructor parameter names
(unit_of_work_factory, id_factory) and execute parameter names (self, command).
Despite its one-command-surface title, it does not enumerate every service method
or forbid additional entry points. The actual
[interpreter source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
also exposes execute_observed for GuardedObservedEffectFold. Parameter-name checks
do not assert keyword-only kinds, defaults or annotations; the actual id_factory
parameter is keyword-only. GuardedObservedEffectFold is covered by the later
inventory assertion, not this test's nine-binding identity table.

An otherwise valid command with empty scopes must raise EffectAttemptFoldDenied,
render exactly scope execution:operate is missing, satisfy the inherited safe-error
checks and leave the factory count zero. Actual execute first calls the
[language validator](../src/control_plane_kit_operations/effect_attempt_fold.py.md);
the shared execution path then checks EXECUTION_OPERATE and translates the fence
before entering the factory. This test proves the selected empty-scope boundary,
not HTTP authentication, current database authority, lease freshness or exhaustive
ordering between every possible invalid input.

The malformed-command table contains twelve candidates: raw object, command,
authority and fence subclasses, hostile outcome fingerprint and worker text,
recovery identity/text, failure code/message/details, and a non-UTF-8 request ID.
Every candidate must raise InvalidOperationCommand with the fixed command-invalid
message before the factory is called. Selected canaries must be absent from safe
rendering. The hostile identity defines permissive equality, but no dispatch spy
counts its use; rejection is not evidence that no hostile method ran. Some changed
failure/transition candidates also violate outcome congruence, so these rows do
not independently isolate every exact-type check from all other invalidity.

The inherited assert_safe_error requires cause and context to be None, combined
str/repr length at most 256 and absence of supplied substrings. assertRaises accepts
exception subclasses. These fixed-message cases are not a universal sanitizer for
arbitrary exceptions or evidence that all internal values are safe to serialize.

Two further commands have matching authority/fence workers of 257 characters or
a lone-surrogate-containing string. They must reach the service's fence-translation
rejection, render exactly execution lease fence cannot identify an effect attempt,
pass safe-error/canary checks and leave the factory untouched. Operations lease
values have a wider worker limit than the effect-attempt representation. The actual
translator requires exact UTF-8-encodable, nonblank text of at most 256 characters,
no NUL, and an exact integer generation from 1 through 2**63-1 before constructing
the [Core fence](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py).
Other command checks still apply; this pair is not an exhaustive Core fence suite.

The positive boundary uses 256 worker characters and generation 2**63-1. The exact
factory AssertionError must escape and the count must be one. This establishes
that the combination passes preflight and reaches the factory. The factory receives
no coordinates, so the test title's unchanged claim is not a recorded downstream
fence comparison. There is no store access, ID allocation or successful result.

The provider-free test parses the language and interpreter files as AST. It collects
literal Import/ImportFrom module strings and rejects nine substring fragments,
including coordinator, provider, http, mcp and execution_lease_recovery. It also
rejects four ast.Name identifiers: provider_request, provider_result, dispatch and
RecoveryAuthority. This is selected lexical dependency coverage, not transitive
dependency analysis, dynamic-import detection, attribute-name inspection or a
runtime absence-of-effects proof. Actual selected entry paths consume Operations
stores and Core values rather than invoking a provider.

The inventory test reads the configured CPK_PACKAGE_MODULE_INVENTORY path or the
repository default, selecting the two module entries into a dictionary. Both must
be owned by operation, with the language's exact export set and interpreter's
single-element export list. It checks the interpreter's nine-member dependency
set, empty optional-external lists, selected protecting-test membership and four
motivation substrings, excluding held. The language must depend on outcome evidence.
Set/dictionary projections do not reject duplicate exports/dependencies or duplicate
module entries; other inventory rows are ignored. These are declarations from the
[module inventory](../../../../docs/architecture/package-module-inventory.json), not
an independent resolution of every import or proof of its atomic/durable prose.

The separate [atomic contract](../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py)
and PostgreSQL owners carry further sum, transaction, replay and rollback evidence.
This companion credits only its seven tests. The real interpreter performs locked
durable reads after factory entry and commits through its unit of work; none of
those operations occurs in these deliberately interrupted factory probes. No
automatic recovery, provider approval or cleanup authority follows from admission.

Read depth: all 444 source lines, seven tests and local helpers; retained full
259-line parent fixture and 518-line language owner; refreshed actual service
entry, scope/fence translation, selected durable paths, Core fence, inherited
safe-error and relevant inventory entries. The broader interpreter owner has not
received a full-source companion review here and remains pending. No imports,
tests, Docker, database, credentials, providers or source edits ran in authoring.
