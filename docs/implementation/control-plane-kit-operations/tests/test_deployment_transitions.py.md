Source: [control-plane-kit-operations/tests/test_deployment_transitions.py](../../../../control-plane-kit-operations/tests/test_deployment_transitions.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 397-line pure unittest suite has eight tests for the graph-pair deployment
transition language. It checks public identity, four classifications, direct
constructor guards, graph-name changes, structural emptiness across five collections,
ingress/authority changes and finite source-purity exclusions. There is no database
fixture, provider execution, credential loading or runtime deployment. The direct
guard invokes unittest.main.

_contract imports the Operations root, checks six expected attributes with hasattr,
then imports the deployment_transitions module. Missing root attributes produce
an AssertionError naming #1624; this is not an optional-import/skip mechanism.
The public test requires those six root objects to be the same objects exported
by the named module and compares DeploymentTransition with the union of
InitialDeployment, UpdateDeployment, TeardownDeployment and NoOpDeployment. It does
not assert module.__all__ equality, reject all other module exports or prevent
subclasses merely by calling the family closed.

_validated calls actual validate_graph and raises a fixture AssertionError carrying
the findings descriptor if the result is invalid. The selected
[ValidatedGraph contract](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py)
defines valid as absence of ERROR findings; warnings do not make the helper fail.
require_valid returns its graph or raises GraphValidationError from the stored
findings. The helper exercises ordinary validation rather than manually inventing
a successful wrapper; it does not assert every possible validation law.

_empty validates a graph with no populated collections and a supplied/default name.
_runtime_graph validates a graph containing one Docker-kind RuntimeRecord named
runtime with owner metadata and no children. Blue/green variants differ by that
metadata, not by a real runtime or deployment. No Docker connection is made by
constructing those values.

_gateway_graph constructs gateway and connector ApplicationBlocks under one
DockerRuntime and calls actual compile_topology. Gateway declares one HTTP provider
and a synthetic literal address http://gateway:8000; connector has no sockets or
endpoints. _PureImplementation.materialize returns a local _MaterializedBlock with
endpoint values using the declared provider's protocol. It does not contact the
address, launch a container or use its block_id/runtime arguments. Missing provider
names would fail through sockets.provider, not an endpoint-probing layer.

_MaterializedBlock is a frozen dataclass with empty environment/configuration/
secret tuples and OWNED_EPHEMERAL lifecycle; absent metadata becomes a fresh dict.
Frozen fields do not make its endpoint/metadata dictionaries deeply immutable.
The actual
[compiler](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py)
materializes the supplied implementations into nodes, records runtime membership
and copies graph-owned ingress/authority values. Its missing runtime-authority-
deliveries attribute falls back to an empty tuple for this local materialized
type. These are typed topology fixtures, not product implementations or runtime
observations.

The five Deploy cases are empty -> blue as Initial, blue -> green as Update,
blue -> empty as Teardown, and identical empty or blue pairs as NoOp. Each assertion
uses isinstance for the expected variant, preserves current/desired wrapper object
identity and requires a GraphDiff. Repeated Deploy calls must produce equal diff
descriptors. That last assertion is repeatability against the same implementation,
not an independent expected diff oracle or proof the factory computes the diff
only once.

The actual
[transition owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_transitions.py)
requires ValidatedGraph instances, calls require_valid for both and computes the
existing diff. Empty diff takes precedence as NoOp; otherwise crossing from
structurally empty to nonempty is Initial, the reverse is Teardown, and all other
different pairs are Update. Deploy classifies to select a variant and the variant's
post-init classifies again, assigning diff as an init=False field. The tests do
not count those calls or claim classification itself executes a program.

Direct-constructor testing accepts the same five valid cases and compares each
computed diff with actual diff_graphs. Passing a fabricated diff keyword must
raise TypeError in all five cases. Four wrong-form examples require ValueError
matching deployment transition: Initial(blue, green), Update(empty, blue),
Teardown(empty, empty) and NoOp(blue, green). Despite the method's every_wrong_form
name, this is not the full Cartesian product of variants and graph-pair forms.
It also does not attempt later object mutation or assert dataclass frozen/slots
metadata.

A runtime referencing missing-node creates an invalid validation result. The test
asserts valid=False and requires GraphValidationError from Deploy and direct
UpdateDeployment with that invalid current side. It does not test every variant,
invalid desired side, raw graph inputs, forged successful wrappers or error-message
redaction. The observed error comes from stored validation findings through
require_valid, not re-running every validator inside the transition owner.

Two differently named but structurally empty graphs must classify as Update with
a nonempty diff. The first change's subject must be graph/graph-name; the test does
not assert total change count or before/after values there. The actual
[diff interpreter](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py)
records a graph-name change before classifying emptiness, and GraphDiff.empty means
no changes. It also supports codec-language mismatch and other structural changes;
those branches are outside this suite's selected assertions.

The empty-boundary test checks Initial for a runtime-only graph and two valid
gateway graphs extended with either NamedPublicIngress or DelegationAuthorityBinding.
The ingress names gateway/control and connector, a synthetic authority reference
and gateway.example.test. The delegation binding names gateway, GATEWAY_PROBE and
a synthetic issuer. No authority is resolved, key issued or hostname published.
The two extended graphs already contain nodes and a runtime, so these cases alone
do not isolate ingress or delegation authority as the sole cause of nonemptiness.

The private-predicate test supplies that isolation with five direct DeploymentGraph
witnesses containing only nodes, only one edge, only runtimes, only ingress or only
delegation authority. Empty graph must return True and each witness False from
_structurally_empty. These witnesses bypass _validated and may lack referenced
nodes/runtime/socket context; they are probes of collection presence, not examples
of valid deployable topologies. Calling the private helper also couples this test
to the current helper name. It enumerates the five current collections rather
than dynamically detecting every future graph field.

For valid modern-surface updates, the suite reuses the same compiled gateway base
and adds only ingress or only delegation authority before validating both sides.
Each transition must be Update with exactly one diff change. The test does not
assert the change's subject/form/payload, reverse removal or modified-value cases.
The actual diff owner compares ingress records by ingress ID and emits one
graph-level delegation-authorities change when that tuple differs; it supplies
the asserted change count rather than the transition owner maintaining a second
diff language.

The purity test resolves the actual transition module file, parses its AST and
collects top-level package roots from all Import and ImportFrom nodes with a
module string, including imports inside functions. The set must exclude ten roots:
the interpreters/secrets/servers packages, docker, fastapi, httpx, mcp, psycopg,
requests and subprocess. Five literal source fragments are also forbidden:
DeploymentProgram, Postgres, UnitOfWork, commit( and rollback(. This test does not
inspect transitive imports, resolve dynamic imports, analyze all call targets or
instrument filesystem/network effects. Relative imports without a module string
are not represented by that ImportFrom branch. Finite syntax/text checks support
the intended pure boundary but are not a universal proof of effect absence.

No test exercises persistence, approval, scheduling, execution history, compensation
or teardown side effects. TeardownDeployment is a graph-pair value in these tests,
not authorization to delete resources. Secret-delivery fixtures are empty and
the suite has no canary-based descriptor/error-redaction assertion. Scope is the
classification contract and selected dependencies; there is no new live exposure
or credential use in documenting it.

Read depth: the complete 397-line suite, all eight tests and every helper/local
fixture were read, together with the complete 131-line transition owner and topology
compiler. Selected root exports, ValidatedGraph/require_valid and missing-node
validation, GraphDiff, diff entry/runtime metadata/ingress/delegation paths, graph
collections and topology/runtime declarations were checked. No full validator,
diff, algebra or graph dependency review is claimed. Validation was documentation-
only: local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
