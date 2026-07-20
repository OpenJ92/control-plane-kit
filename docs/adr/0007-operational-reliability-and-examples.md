# ADR 0007: Operational Reliability And Examples In Every Roadmap

## Status

Accepted.

## Context

`control-plane-kit` is not only a library of pure values. It is intended to
describe, validate, interpret, run, inspect, and eventually mutate live systems.

That means each roadmap vertical should ask:

```text
How do we know this works when it is running?
How do we know what happened when it fails?
What can UI, MCP, CLI, or tests observe afterward?
What example teaches and exercises the new behavior?
```

Examples matter more in this package than in many libraries because the
abstractions are intentionally structural. A good example is both teaching
material and a small executable specification.

## Decision

Every roadmap vertical must include an operational reliability and examples
checkpoint before the roadmap branch merges to `develop`.

The checkpoint asks:

- What health/status surfaces exist?
- What bounded logs or events exist?
- What activity history exists?
- What failure modes are visible?
- What cleanup behavior exists?
- What retry/resume behavior exists?
- What examples now exist?
- What examples should be added before moving on?
- Which examples should be exercised in tests or smoke scripts?

## Example Accrual

Examples should accumulate as the package grows.

They should not all be large demos. Prefer a ladder:

```text
tiny example
  teaches one object or law

composition example
  shows two or three concepts working together

runtime smoke example
  proves interpretation works in Docker or another runtime

roadmap capstone example
  demonstrates the vertical's coherent behavior
```

Examples can be:

- Python files under `examples/`,
- test fixtures,
- README snippets,
- graph descriptor fixtures,
- smoke scripts,
- or small server blocks.

When an abstraction is hard to explain, write a small example before expanding
the abstraction.

## Operational Reliability Note

Roadmap PRs and non-trivial child PRs should include an operational note when
they affect runtime behavior:

```text
Operational reliability

- Health/status:
  ...
- Logs/events:
  ...
- Failure modes:
  ...
- Cleanup:
  ...
- Retry/resume:
  ...
- Examples added or updated:
  ...
- Examples still missing:
  ...
```

For pure documentation or naming PRs, this can be short:

```text
Operational reliability: no runtime behavior changed.
```

## Relationship To Activity History

ADR 0006 says the control-plane/home server should preserve structured activity
history. This ADR says each roadmap vertical should also ask whether its new
behavior is observable and taught by examples.

Together:

```text
Activity history records what happened.
Operational reliability explains whether we can understand and recover.
Examples prove and teach the behavior.
```

## Consequences

- Roadmap completion requires more than code and unit tests.
- Examples become a maintained asset.
- Runtime work must include health, failure, and cleanup thinking.
- UI/MCP/CLI work gets clearer query surfaces.
- Future maintainers can learn the package by running small cases.

## Non-Goals

- This ADR does not require every roadmap to add a large demo.
- This ADR does not require production-grade observability infrastructure.
- This ADR does not require metrics, tracing, or dashboards before the package
  needs them.

## Review Checklist

At the end of a roadmap vertical, answer:

- What example now best teaches this roadmap result?
- Can the example be run?
- Is it covered by tests or a smoke check?
- What health/status/log/event surface does it show?
- What failure mode does it demonstrate or protect against?
- What cleanup behavior is exercised?
- What example should the next roadmap inherit?

