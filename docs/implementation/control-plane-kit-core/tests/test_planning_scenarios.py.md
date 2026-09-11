Source: [control-plane-kit-core/tests/test_planning_scenarios.py](../../../../control-plane-kit-core/tests/test_planning_scenarios.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Catalogue expectations checked against the real compiler

The planning tests validate every current/desired graph, check unique scenario
IDs, and compare the compiled operation multiset with the catalogue's typed
expectations. They compare exact maximum risk and structural readiness, and
require each declared dependency to occur in the compiled plan. Additional
dependencies are allowed; the test does not require exact equality of all edges.

Repeated compilation must produce equal plans without changing graph
descriptors. A small ownership assertion checks that scenarios lack selected
workspace/workflow attributes. These are meaningful pure compilation witnesses,
not a complete proof of no side effects in arbitrary future materializers.

Execution-expectation tests check one expectation per planning scenario,
successful-case summaries, health-observation shapes and the three special
no-run dispositions: unchanged topology, database cutover awaiting external
readiness and unsupported change awaiting review. Negative constructor tests
protect declared event membership/scope, no-run evidence and advancement
restrictions, and linkage to canonical planning operations.

The named acceptance-case test checks deterministic values, unique IDs,
canonical scenario coverage and the presence of six failure/uncertainty/
compensation case names. It does not inject a failure, execute those cases,
read an event journal or prove that each name has distinct behavior. The
[source catalogue](../src/control_plane_kit_core/planning/scenarios.py.md)
currently wraps one base scenario for those names.

Full 381-line test file, helpers and full catalogue source read for this note.
Actual approval, admission, provider readiness, durable history and graph
advancement remain obligations of their implementation owners. No database,
provider, runtime or executable validation was run during companion authoring.
