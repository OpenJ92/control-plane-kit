# Control Plane Kit Operating Model

Status: Current

Canonical contract: `cpk-agent-contract/v1`

This is the normal issue-to-merge loop for Control Plane Kit. `AGENTS.md` owns
the shared product, authority, memory, and validation laws.

## Normal Loop

```text
current issue contract
  -> feature branch from the accepted base
    -> inspect current public contract, source, and owning tests
      -> choose the smallest ownership-local proof
        -> implement the bounded change
          -> owning Docker package suite
            -> PR decision log
              -> independent findings-first review
                -> merge and durable handoff
```

The issue controls scope. A source dry run should establish what already exists,
the exact missing behavior, the owner, the source ceiling, and the validation
boundary before implementation grows.

Use focused target-red evidence when the issue requires migration/parity proof
or when it is necessary to distinguish genuinely missing behavior from broken
collection or apparatus. It is not mandatory ceremony for every change.

## Issue Contract

Before implementation, the governing issue should make these facts discoverable:

- user-visible goal and acceptance;
- owner and source/test ceiling;
- dependency topology, accepted base, branch destination, and downstream handoff;
- authority class, including whether Docker, shared services, providers,
  credentials, or destructive actions are permitted;
- owning Docker suite and exact prerequisites;
- exclusions and stop conditions.

If one of these affects correctness and is missing, stop and ask on the issue.
Do not reconstruct hidden decisions from chat or local artifacts.

## Branch Shape

Default:

```text
develop
  -> codex/<issue-id>-<slug>
    -> PR into develop
```

Use a roadmap integration branch only when a parent issue explicitly establishes
an ordered multi-PR vertical. In that case, child PRs target the named roadmap
branch and the roadmap PR targets `develop`. Do not infer this topology from a
roadmap label or create a draft roadmap PR automatically.

## Proportional Tests And Review

Start with existing ownership-local behavioral tests. Add the smallest test
that would catch the concrete regression or expose the missing public behavior.
Tests should not duplicate Core/Operations semantics in servers or live
harnesses, treat fixture IDs as runtime constants, or enforce helper names and
source layout.

Review is findings-first:

1. list concrete blockers in severity order;
2. state the smallest correction;
3. record residual risks;
4. conclude `PASS` or `HOLD` and name the next permitted boundary.

Do not create a separate hardening PR unless review identifies a coherent risk
surface that is independently useful and reviewable.

## Assigned-Role Protocol

When multiple agents are assigned:

```text
North      coordinates topology, releases, live/shared authority, and merge
Vale       implements within the released source/test boundary
Meridian   reviews independently and remains read-only unless reassigned
```

Assignments link the governing GitHub artifact and state scope, coordinate,
suite, prerequisites, authority, stop conditions, return artifact, and reviewer.
Implementers send a short stage and durable artifact link when work is ready.
The coordinator acknowledges receipt and actively waits or polls; silence is
never treated as approval or completion.

## Crash-Safe Checkpoints

Before a long Docker suite or other interruption-prone gate:

1. commit and push substantial reviewed work;
2. record the checkpoint on the issue or PR;
3. label it candidly as unvalidated;
4. preserve the ordinary suite command and prerequisites;
5. after the gate, record the terminal result and classification.

A commit hash or local log identifies evidence but does not replace its GitHub
decision record. Apparatus failure earns no behavioral credit and triggers the
stop rule in `docs/TESTING.md`.

## PR Decision Log

Every non-trivial PR gives the user a compact account:

```text
Decision log

- Chosen shape:
- Why:
- Alternatives rejected:
- Ownership:
- Important snippets:
- Docker validation:
- Risks and residuals:
- Handoff:
```

Add security, data-safety, mathematical-design, or operational-history sections
only when those surfaces are material. Include no-security-change explicitly
when appropriate.

## Durable Handoff

On merge, record:

- merged PR and develop coordinate;
- capability now available;
- owning validation and result;
- security/data/history consequences;
- remaining risk or deferred work;
- exact dependent issue and next public coordinates it may rely on.

Update or explicitly supersede stale issue text before dependent work begins.

## Stop And Split

Stop or split when the change crosses unrelated owners, introduces a new public
concept, requires a separate authority/security/data decision, or cannot be
explained in one concise PR decision log. Prefer one coherent implementation
over a large test harness that attempts to prove several packages at once.
