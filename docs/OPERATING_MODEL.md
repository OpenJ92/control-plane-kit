# Control Plane Kit Operating Model

Status: Current

Canonical contract: `cpk-agent-contract/v1`

This is the normal issue-to-merge loop for Control Plane Kit. `AGENTS.md` owns
the shared product, authority, memory, and validation laws.

## Operating an installed CPK

Use the named MCP connection designated for the task. Keep credential values
in configured custody. The setup administrator grants workspace access, the
operator authors the graph and requests a plan, the human approver reviews
the decision, and the worker claims admitted work with its own permissions.

1. Open a fresh client, read the server instructions and discover its tools.
   Read a known workspace and its expected revision or Current graph to identify
   the target. The restricted grandparent profile advertises four saved-state
   reads. A read-only principal on the standard profile can see a different
   catalogue and still receive a permission denial. Check the registered
   products and runtime authority through the authorized reads available. If a
   required tool or permission is missing, record the gap and stop that step.
2. Save the draft and retain its workspace, draft ID, revision and graph ID.
   Read that revision back. Select it as Desired and prepare against the
   observed Current and Desired graph IDs, projection IDs and Desired generation.
   Keep the preparation key and returned session, plan and approval identities.
   Follow the recorded outcome: no changes, review blocked, or approval required.
3. For the Hello workflow, the human decides on the exact pending plan in the
   website. The operator reads back the decision. Where the installed profile
   exposes admission, submit the approved plan with its recorded approval and
   caller-owned key. Keep the queued execution request ID. The worker claims
   the request separately.
4. Match the request ID in that plan's run history. Read the terminal result,
   compare Current with the target revision, then check the intended application
   or management route. After a lost reply, use the original durable identities.
   An empty run page can coexist with a committed, unclaimed request. If the
   evidence cannot identify the outcome, report it as unknown and stop.

For recursive deployment, the selected sibling acts as grandparent. Use its
dedicated child-deployment workspace and keep the Hello workspace separate.
After the child is ready, connect under a distinct MCP name with a scoped child
identity. Before any child write, read a known child-only workspace or revision
through a fresh child client. The grandparent owns the installation run and
its gateway observation. The child owns the application plan and run.
[Target decision](https://github.com/OpenJ92/control-plane-kit-servers/issues/163#issuecomment-6907372519) · [Child login and handoff](https://github.com/OpenJ92/control-plane-kit-servers/issues/163#issuecomment-6907614504)

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

Follow the source-reading requirement in `AGENTS.md`: after governing-test
inspection, trace the selected entrypoint and imports through values,
transformations, authority/effects, and result/history before designing a change.
Record a few source anchors and dependency versions in the existing issue or PR.
Keep current and proposed paths separate, identify the smallest missing connection,
and distinguish selected code from merged, published, or live evidence. This is
the existing dry run, not an additional gate or permission to run effects.

Apply the source-traversal cadence in `AGENTS.md`: trace at parent-to-child
issue design, reuse and confirm the relevant trace at child implementation
planning, and reread affected paths when code or evidence changes. An unchanged
trace is not repeated for routine updates or handoffs. Independent review still
checks changed code and relevant boundaries without duplicating the whole tour.

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

Adjacent Core/Operations issues normally share one feature branch and PR, with
separate ordered commits. Record batch membership, dependency order, integration
target and acceptance boundary. Keep each issue's source/test design and independent
review; dependent work may use preceding reviewed commits before an intermediate
merge. Split at meaningful architecture, ownership, review or mandatory milestone
boundaries. Cross-repository work retains separate owning PRs and gates.

Run local tests throughout implementation at the useful scope, including broader
checks when warranted. Consolidate slow full GitHub Operations CI into one final
batch run rather than one per issue. Preserve other required package, integration,
parity and live gates at their owning boundaries. Failures, material changes or
unresolved findings justify remote reruns. Link every issue and close it only after
its criteria and the combined acceptance/merge pass; focused evidence is not full acceptance.

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
