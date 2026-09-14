Source: [control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# An acceptance catalogue expressed as values

This module constructs eleven graph-transition examples: fresh deployment,
backend switching, load-balancer scale-out, rate-limiter insertion, request
observation, movement between runtimes, database endpoint switching, partial
scale-in, full teardown, no change and an unsupported implementation transition.
Small pure materializers and socket declarations build their graphs. Docker
and endpoint names here are model values; catalogue construction starts no
container or network request and supplies no product image implementation.

Each PlanningScenario retains current/desired graph values and an expectation:
a set-like list of operation type/target identities, required dependency edges,
maximum risk and structural readiness. Approval comments are teaching text,
not decisions. Construction checks types, unique expected operations and
dependency membership; it does not validate the graph or run the activity
compiler. The [tests](../../../tests/test_planning_scenarios.py.md) perform
that comparison against the real [compiler](compiler.py.md).

operation_expectation deliberately forgets activity IDs and operation material
to compare type plus target. It handles the catalogue's node/runtime/socket
operations and ReviewChange; it is not a total serializer for all ActivityOperation
variants. Ingress allocation/removal and data destruction are not handled.
Field review subjects collapse to their owning node/runtime/edge or graph.
Generic operation_type construction requires a Python type, not membership in
the closed operation algebra.

Execution expectations add typed eligibility, approval/admission, run,
semantic events and a partial order, observation expectations and graph
advancement. The four eligibility forms are executable, no changes, external
readiness gated and review blocked. The canonical database cutover expects
approval but no admission or run until external readiness is established.
No-change and unsupported-transition cases also expect no run. These are
acceptance requirements; they do not grant authority or certify provider support.

Default successful expectations require approved admission, a succeeded run,
per-operation start/success events, selected dependency event ordering,
application-health observations for WaitForHealthy targets and graph
advancement after run success. Constructor laws reject undeclared event-order
references, mismatched event scope, runtime evidence on NoRunExpected,
advancement without succeeded-run status and admission without approved
expectation. ExecutionScenario ties referenced operations and selected
eligibility rules back to its planning expectation.

These checks are not a complete execution state machine. For example, event
order validates endpoints rather than proving an acyclic order, and typed
observation fields are not a full probe-kind/outcome compatibility check.
Frozen values do not independently normalize or deeply freeze every supplied
collection. Core expectations must not replace Operations' durable admission,
event, observation or advancement rules.

execution_scenario_cases returns the canonical cases plus six named failure,
uncertainty and compensation cases. Those six currently wrap the same fresh
deployment execution scenario; their names do not encode fault injection or
distinct failed-run expectations. A downstream acceptance runner must implement
the named case behavior and provide its evidence.

The runtime-movement fixture uses DRY_RUN contexts. Database switching changes
an environment-backed requirement between graph endpoints, not database
migration or freshness proof. _retain is a small fixture slicer that rebuilds
selected nodes, edges and runtime children; it is not a general graph-retention
or resource-cleanup operation. Strings and error representations are not
universally bounded or redacted. No clock, persistence, credentials, effects or
live acceptance is owned here.
