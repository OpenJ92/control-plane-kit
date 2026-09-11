Source: [control-plane-kit-operations/tests/test_effect_attempt_reconciliation_interpreter_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_reconciliation_interpreter_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These four tests combine two service-entry sentinel tests with two shared
architecture-policy tests for the reconciliation language and interpreter. Much
of the 584-line file is literal import/call surface data. It does not return a
working UoW, run SQL, invoke a real observer or execute a successful fold. The
[runtime fixture](runtime_effect_reconciliation_fixture.py.md) supplies captured
symbols, presence guards, command defaults and inherited error checks.

FailIfUnitOfWork counts calls and raises the same stored AssertionError before
returning any context. FailIfObserver counts observe calls and raises a newly
constructed fixed error. FailIfFold counts execute calls and raises; it has no
execute_observed method. The actual interpreter's fresh path calls execute_observed,
so this fold double is not a complete spy for that interface. An accidental later
call would fail the test through a missing-method error, but fold.calls == 0 alone
does not demonstrate that all fold interfaces went untouched. The tested paths
are expected to stop before any such dispatch.

service requires the service symbol, passes the supplied factory and uses
observer or FailIfObserver() and fold_service or FailIfFold(). Falsey supplied
objects would select defaults; the tests use ordinary truthy sentinel instances.
Construction of the actual service also creates a SecretUseAuthorizationService
with that factory. Its inspected constructor merely retains the factory; it does
not open a UoW. This source context explains why constructing the service does not
itself invalidate the intended preflight boundary.

The invalid-entry matrix has six candidates: a raw object, a forged command
subclass, an exact command with hostile request text, one carrying an identity
subclass, an exact forged nested RunId containing hostile text, and an exact
command with all fields missing. bypass and forge_exact allocate objects directly
and assign fields without constructor admission. Each must raise the exact fixed
InvalidOperationCommand message, pass safe-error checks, leave the factory,
observer and fold counters zero and leave HostileText's dispatch list empty.

HostileText instruments __class__ access, length and encode only. Its instances
occur in the request-text and nested-run cases; the other subclasses do not spy
on arbitrary attribute/equality behavior. The shared cleared list does not turn
all six cases into a comprehensive hostile-object test. In particular, missing
top-level fields and the supplied nested forgery do not enumerate every possible
missing nested field or unexpected exception path.

A separate normally constructed command with empty scopes must raise
EffectAttemptReconciliationDenied with scope execution:operate is missing, pass
the inherited safe-error helper and leave the same counters zero. This establishes
that the selected scope rejection precedes factory invocation; it is not a stored
claim, grant, lease or cross-workspace authorization test. The
[language owner](../src/control_plane_kit_operations/effect_attempt_reconciliation.py.md)
can admit empty scopes as value data, while the interpreter's entry requires the
operation scope after revalidating the command.

The valid-entry test supplies the ordinary command and requires the identical
factory.error to escape, exactly one factory call and zero observer/fold counters.
This proves arrival at the injected boundary, not a successful transaction,
database cleanup, observer-outside-transaction behavior or durable replay. The
factory raises before a UoW object is returned or entered. The test makes no
claim about secondary exceptions from __enter__/__exit__, commit or rollback.

The shared-policy value test builds four policies and requires exactly the two
policy classes ExactImportSurfacePolicy and ExactCallSurfacePolicy among them.
The builder creates one import and one call policy for each of the language and
interpreter, with fixed package-relative paths/module names, distinct PolicyId
strings, RuleId("exact") and fixed finding messages. This checks admitted policy
construction and shape. It does not inspect source or demonstrate a rejected
mutation; the next test performs the actual source analysis.

That test requires both owner files under the package's src directory, reads
their UTF-8 text, obtains PythonSourceFacts from analyze_source and requires an
empty evaluate_policies result. The language policy lists twelve import entries
and 31 lexical call occurrences, including five bounded-text calls and fourteen
type calls. The interpreter literals include import coordinates for Core and
Operations values/services and calls to owned reads, authorization, observer and
guarded-fold interfaces. They deliberately repeat call targets occurring at
multiple source sites; they are not a set of callable capabilities.

The actual architecture-testing dependency is the clean sibling coordinate
7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef required by the owning
[package harness](../../../../control-plane-kit-operations/test.sh). Its analyzer
parses with the executing Python's stdlib AST grammar and derives lexical names
from imports and attribute chains. A ResolvedCallTarget such as self._observer.observe
means a syntactically derived name, not resolution of that object's runtime type
or proof of its implementation. Non-name-root calls, conflicting aliases and
certain wildcard cases produce unresolved targets; these expected lists contain
only resolved targets.

The dependency's exact policies sort projected imports/calls and compare tuples.
They retain repeated occurrences but discard source order in that comparison.
Thus these tests can catch an added call/import or changed multiplicity/name under
the analyzer, not control-flow order, reachability, argument identity, transaction
extent or effects behind an admitted call. The language's twelve/31 figures are
source-occurrence counts, not invocation counts. No mutation experiments are run
by this file to establish every detector's sensitivity.

The effect-free wording in the test title must be read narrowly. The admitted
interpreter surface explicitly contains self._observer.observe,
self._fold_service.execute_observed and secret-use authorization. The policy
forbids deviations from its reviewed lexical list; it does not prove that those
calls are pure, read-only, authorized, bounded or outside database transactions.
Nor does it analyze the transitive bodies of imported constructors, stores or
injected adapters. Actual effect ownership and durable sequencing need their
separate service/PostgreSQL tests and source review.

The [language contract tests](test_effect_attempt_reconciliation_contract.py.md)
separately inspect signatures/type hints/root publication and pure observation
worlds. This file imports inspect and EffectAttemptFoldResult but does not use
them to perform another signature or result test. Runtime input/scope sentinels
and lexical policy evidence are complementary, not interchangeable claims of
whole-interpreter correctness.

Read depth: all 584 source lines, four tests, every sentinel/forgery/service/policy
helper and literal expected surface; full language/fixture context; actual
interpreter constructor/entry/fresh-call context and secret-authorizer constructor;
selected analyzer name resolution/fact extraction and exact-policy comparison
at the harness's checked clean dependency coordinate. No full interpreter or
architecture-testing package review is claimed. No tests, application imports,
database connections, Docker, providers, credentials or source/dependency edits
were executed while authoring this companion.
