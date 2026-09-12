Source: [control-plane-kit-operations/tests/test_activity_run_retry_interpreter_contract.py](../../../../control-plane-kit-operations/tests/test_activity_run_retry_interpreter_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five tests protect the retry interpreter's export, selected syntax/ownership
and one real scope-denial boundary. They import the actual service and read source/
inventory metadata. They do not run PostgreSQL setup, a successful retry, persisted
replay, provider effects or a concurrency scenario. Importing the shared fixture
still requires the package's dependencies; this is not a host-only fallback suite.

The first test requires the service to exist, be identical to the Operations root
export and report the exact interpreter module. It does not audit every root export
or establish that importing the full package is dependency-free. The shared
[fixture](activity_run_retry_interpreter_fixture.py.md) masks only absence of the
exact interpreter module, not unrelated nested dependencies.

The constructor/surface test parses the current source, selects the named top-level
class and collects its direct ordinary FunctionDef methods. It expects exactly
__init__, execute and _plan_result, and execute's ordinary positional parameter
names self and command. Despite its title, it does not inspect the constructor's
parameter list, defaults, keyword-only arguments or annotations. It also does not
prove complete runtime dispatch behavior or forbid every alternate/dynamic callable
shape. The actual service constructor receives a UoW factory and keyword-only ID
factory; that fact comes from source, not a full signature assertion in this test.

The behavioral test instantiates PostgresActivityRunRetryFixture without calling
setUp and uses only retry_command. For empty scopes and RENEW_CLAIM alone, the
actual service must raise RunLifecycleDenied. Its injected UoW factory raises
AssertionError if called, so these two negatives prove denial before factory entry.
The ID factory returns unused-id but is not instrumented: do not turn this into a
separate zero-ID-call assertion. No configured database URL is needed by this path
and no database fixture connection is established.

The imported safe_error helper checks that these denials have no cause/context,
combined str/repr length at most 512 and no fixture authority-reference canary.
This is selected error evidence, not universal serializer/log redaction. The actual
service checks OPERATE before fingerprint/UoW work. The test does not prove positive
authorization, authenticate RecoveryAuthority, verify lease expiry or approve a
retry; supplying a scope value is not authentication of its caller.

The inventory test uses CPK_PACKAGE_MODULE_INVENTORY or the repository default
[module inventory](../../../architecture/package-module-inventory.json). It requires
one interpreter row, the one public service export, exactly interpreter and
transformations semantic roles, and membership of the pure retry and shared-support
dependencies. It does not compare the entire dependency list or assert every owner,
destination, optional dependency or protecting-test field in that row. The inventory
is declared ownership evidence, not an observed complete import graph.

The final test requires the top-level ImportFrom of the exact shared-support module
to name precisely locked_recovery_approval, require_recovery_eligible_journal and
require_replay_run_evolution. All three imported runtime objects must be identical
to the support module's objects; the lease-recovery interpreter must share the
same evolution helper. A finite list of seven helper names must be absent from
top-level synchronous/asynchronous function definitions in the retry interpreter.
Those checks prevent the named duplicates and establish selected binding identity;
they do not prove call order, all call sites, transitive ownership or absence of
equivalent logic under a different name.

Actual source calls the shared approval/journal checks during fresh retry and the
approval/evolution checks on replay. Their internal validation and durable locking
are not exercised by these five tests. The
[support companion](../src/control_plane_kit_operations/_execution_lease_recovery_support.py.md)
describes that boundary; PostgreSQL first/replay, eligibility/rollback, store-boundary
and concurrency tests own separate evidence. Structural checks here must not be
presented as proof of transaction atomicity, preserved history, safe effect retry,
or lease/approval correctness.

Read depth: full 188-line test, full 218-line retry fixture and 572-line parent
including Sequence, safe_error and setup; full 466-line interpreter and actual
inventory row, with selected RecoveryAuthority context. The pure command/result
owner and its complete tests were retained from prior review. No test execution,
application import, source/pin edit, database setup, credential access or provider/
runtime action occurred. Documentation introduces no security surface and does not
claim this suite ran or passed during authoring.
