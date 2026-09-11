Source: [control-plane-kit-operations/tests/test_effect_attempt_start_interpreter_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_start_interpreter_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 647-line suite contains eight unittest methods covering the start service's
public surface, command/scope/fence rejection before a unit of work, and static
language/import/inventory constraints. It uses the actual service with a factory
that always raises when called. No test in this file enters a working transaction,
returns a persisted start result, verifies replay, dispatches a provider effect or
checks restart history. The direct-execution guard invokes unittest; this
documentation pass did not run it or import the application.

The suite inherits
[EffectAttemptStartFixture](effect_attempt_start_fixture.py.md) and TestCase.
service first checks service presence, then constructs EffectAttemptStartService
with the supplied unit-of-work factory and an ID factory returning unused-event-id.
That ID factory is a constant callable, not an invocation spy. FailIfUnitOfWork
stores one AssertionError and a call counter; each invocation increments the
counter and raises that same error before returning any context manager. This
distinguishes pre-factory rejection from reaching the transaction boundary without
providing a fake store or transaction implementation.

malformed_fence allocates an exact ExecutionLeaseFence with object.__new__ and
assigns worker_id and generation without constructor admission. Local bypass
helpers similarly assemble command instances without __post_init__. These seams
let the service's revalidation be tested independently of normal construction.
All inherited safe-error assertions require absent cause/context, combined
str/repr length at most 512 and absence of truthy supplied canaries. They do not
inspect traceback or log output or sanitize the error themselves.

The surface test checks identity with the Operations root export and the service's
owner module. It compares signature parameter names to unit_of_work_factory and
id_factory for construction, and self/command for execute. It does not compare
annotations, parameter kinds, defaults or every public attribute, so the method's
"one command surface" name is narrower in executable assertions.

The scope test constructs a valid command whose authority has an empty scope
tuple. execute must raise EffectAttemptStartDenied with the fixed missing-scope
message and safe rendering, while the factory counter remains zero. This checks
EXECUTION_OPERATE rejection before transaction entry; it does not query a durable
claim or establish whether any worker holds a current lease.

The raw-command test builds nine initial candidates: an ordinary object, a
constructor-bypassed command subclass, a fingerprint str subclass, an exact
forged intent with a foreign request ID, a class-access-hostile intent, a foreign
but validly shaped fingerprint, a worker str subclass, non-UTF-8 request text and
a newline-containing worker with a forged fence. The candidates retain other
fields from a valid command. Each must produce the fixed InvalidOperationCommand,
safe canary rendering and zero factory calls. The shared hostile-intent dispatch
list must remain empty after every case.

The imported
[intent helpers](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py)
use forge_exact to assign fields without post-init and class_access_hostile_copy
to create a subclass that records and raises on __class__ access. Empty local
HostileCommand/HostileText subclasses do not themselves override those operations.
These are specific hostile shapes and one request-coordinate forgery, not an
exhaustive assertion over arbitrary malformed exact-type commands.

The same method includes a lawful positive control: the valid command must reach
the factory exactly once and propagate its identical AssertionError. It then
redefines its local bypass helper to replace only intent and runs four deep
coordinate cases: hostile run wrapper, run text, activity wrapper and activity
text inside exact outer intent/source values. Wrapper probes record selected
class/value access, equality and hashing; text probes additionally track strip
and encode. Each dispatch list is cleared immediately before the service call.

For each deep case, the test temporarily replaces the start-language module's
runtime_effect_request_for_intent binding with a callable that records invocation
and raises. It captures BaseException from execute and restores the original
projection in finally. The captured exception must have exact type
InvalidOperationCommand, the fixed message and safe rendering; coordinate hooks,
projection calls and factory calls must all remain absent. These assertions cover
rejection before those particular seams. The global module-binding replacement
has explicit restoration, but no isolation against concurrent callers.

The actual
[start-language validator](../src/control_plane_kit_operations/effect_attempt_start.py.md)
performs exact command and nested coordinate checks before intent projection.
The selected actual
[service entry and fence translation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
then check EXECUTION_OPERATE and effect-fence representability before invoking the
unit-of-work factory. The suite's raw bypasses exercise that revalidation, rather
than relying on a command constructor having already rejected the candidate.

The fence-translation test has four rejections. A 257-character worker and a
surrogate-containing worker are passed through normal authority/fence builders;
their commands construct, but service translation must reject them. A NUL worker
and generation zero use malformed_fence to bypass the ordinary fence constructor.
Each service error must use the fixed "execution lease fence cannot identify an
effect attempt" message, satisfy safe-error assertions and leave factory calls
at zero. The NUL/generation cases distinguish command shape admission from the
service's stricter fence checks.

The maximum-coordinate test uses a 256-character worker and generation
2**63 - 1. It requires the identical factory AssertionError and one factory call.
Despite the method name's "unchanged" wording, the factory receives no fence
argument and the test captures no translated value; it proves admission to that
boundary, not explicit preservation of each translated coordinate. A separate
assertion rejects generation 2**63 in the normal ExecutionLeaseFence constructor.
No successful unit-of-work or persistence assertion follows either control.

The exact-surface test reads the start-language source as UTF-8 and passes it to
architecture_testing.analyze_source with fixed path/module coordinates. It applies
one ExactImportSurfacePolicy and one ExactCallSurfacePolicy and requires an empty
finding tuple. These policies govern the language module only, not the interpreter
call surface. The expected import tuple records module, imported name and alias;
the expected call tuple includes repeated occurrences of builtins, constructors,
validators, projection/fingerprint functions and value.encode.

The inspected architecture-testing helpers compare sorted occurrence tuples, so
multiplicity is preserved while source locations and ordering are excluded from
the surface comparison. Their analyzer uses Python's AST and syntactic import
aliases to resolve name/attribute call targets. This is a lexical constraint,
not a runtime call graph, transitive-effect proof or control-flow/equivalence test.
The repeated expected calls must not be described as a deduplicated allowlist.
The selected helper sources matched the external commit pinned by the Operations
test runner; no analyzer or policy execution was performed during this review.

The provider-free test separately AST-parses both language and interpreter files.
It collects ImportFrom module strings and Import alias names, rejecting any
containing coordinator, cpk_server, provider, gateway, http, mcp or network. It also
requires AST Name identifiers to exclude provider_request, provider_result and
dispatch. These finite syntax checks do not inspect runtime imports, transitive
dependencies, string-based calls or every attribute name. Their broad test name
does not establish universal absence of external effects.

The inventory test loads JSON from CPK_PACKAGE_MODULE_INVENTORY when set, otherwise
from docs/architecture/package-module-inventory.json under the repository root
derived from __file__. It selects entries for the language and interpreter into
a dictionary keyed by module. It requires both keys and owner="operation" for each.
It compares language exports as a set of eight names, interpreter exports as the
single-element service list, and both optional-external-dependency lists to empty.
Internal dependencies are compared as sets to the explicit nine-module language
and ten-module interpreter expectations.

It also requires the command-contract protecting-test path for the language and
both interpreter-contract and PostgreSQL start-intent test paths for the
interpreter. These are membership assertions, not execution of the referenced
tests or proof that they protect every behavior. Set conversion ignores ordering
and duplicates; dictionary construction can overwrite duplicate module entries.
The test does not cross-check those inventory dependencies against analyzed imports
or validate every inventory field. The selected current
[inventory entries](../../../architecture/package-module-inventory.json)
were read for this companion without modification.

The selected
[Operations test runner](../../../../control-plane-kit-operations/test.sh)
pins architecture-testing source, mounts it read-only and adds its src directory
through PYTHONPATH; it also supplies the inventory path for the container layout.
This explains the unconditional test-only architecture import and environment
override. It is configuration context, not evidence that the runner, Docker,
PostgreSQL or this suite ran during the documentation task.

Read depth: the complete 647-line suite, all eight tests, surface constants and
local helpers were read. Full start fixture208, language211 and command-suite428
context was retained. Selected actual service admission/fence translation,
intent forge/class/deep-coordinate helpers, inventory entries and runner settings
were checked. Selected external architecture analyzer/policy helpers were read
and compared with the runner's pinned source; neither full external module nor
the full start interpreter was reviewed for this slice. Validation was limited to
local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
