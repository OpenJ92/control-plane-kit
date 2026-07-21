# Core Extraction Notes

This package is extracted by law, not by mechanical copying.

## Source Of Truth

The governing frozen evidence lives in the parent repository:

```text
artifacts/extraction/reference-baseline.json
artifacts/extraction/reference-tests.json
artifacts/extraction/reference-law-ownership.json
artifacts/extraction/reference-demos.json
artifacts/extraction/parity-manifest.json
artifacts/extraction/parity-validation-report.json
docs/learning/extraction/run-0001.md
```

## Law Card Shape

```text
frozen reference:
stable law:
observable behavior:
negative cases:
obsolete assumptions:
successor owner:
successor test:
evidence status:
```

## Pure Kernel Boundary

The first migrated language is:

```text
DeploymentTopology
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

Runtime mutation, durable execution, recovery, observations, product servers,
entrypoints, and external-effect adapters remain later extraction milestones.
