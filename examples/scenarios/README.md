# Planning Scenario Corpus

These scenarios are reusable compositions over the core topology and planning
algebras. They are not new deployment primitives.

```text
atomic values
  DeploymentGraph
  StructuralChange
  ActivityOperation
  ActivityDependency
  RiskLevel

composed acceptance fixture
  PlanningScenario
    current graph
    desired graph
    expected operations
    required dependency edges
    maximum risk
    execution-readiness expectation
```

Roadmap 0007 interprets each graph pair into a durable, non-executing activity
plan. Roadmap 0008 should reuse the same scenarios and add expected runtime
events and observed-state transitions. Roadmap 0009 should reuse them through
HTTP, MCP, CLI, and visual-editor boundaries.

The corpus includes both executable-looking transitions and deliberate review
boundaries. A scenario does not grant permission to perform effects. Approval,
execution policy, capability checks, and runtime verification remain separate
layers.

## Catalog

The catalog deliberately grows from small lifecycle work to structural and
safety-sensitive transitions:

1. fresh deployment;
2. backend switch;
3. scale out behind a load balancer;
4. insert a rate limiter;
5. add a request observer through a multiplexer;
6. move a service between runtimes;
7. switch between pre-provisioned database endpoints;
8. remove an inactive backend;
9. tear down a deployment; and
10. reject an unsupported implementation-kind transition until reviewed.

The exact node names are teaching data. The stable contract is typed operation
shape, target identity, dependency ordering, risk, and execution readiness.

The database scenario is deliberately a cutover, not a migration. Both
endpoints already exist. The corpus does not claim that starting a database
copies data, catches up replication, validates schema compatibility, or makes
retirement of an old database safe.

## Downstream Extension

Roadmap 0008 should extend scenario expectations rather than replace them:

```text
ExecutionScenarioExpectation
  = ScenarioExpectation
  x expected ActivityEvent sequence/partial order
  x expected ObservedState changes
  x expected compensation evidence
```

Roadmap 0009 should drive the same workflows through authenticated adapters and
compare their projections with the transport-neutral result. HTTP, MCP, CLI,
and UI tests should not own separate graph-transition truth.
