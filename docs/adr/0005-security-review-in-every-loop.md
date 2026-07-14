# ADR 0005: Security Review In Every Loop

## Status

Accepted.

## Context

`control-plane-kit` describes and eventually mutates running systems. It will
handle topology, runtime state, control routes, secrets, live traffic blocks,
MCP tools, and activity execution. These are powerful surfaces.

Security therefore cannot be a late checklist. It must be part of the roadmap
loop, issue loop, PR decision log, tests, and handoff.

The user should not be expected to personally know every security implication.
The agent must surface important security decisions plainly and repeatedly.

## Decision

Every meaningful loop must include an explicit security review.

This applies at four levels:

```text
roadmap node
  What security surfaces will this vertical introduce or change?

child issue
  What specific access, auth, secret, network, or mutation behavior changes?

pull request
  What security-relevant decisions were made and what tests protect them?

handoff
  What security risks or assumptions does the next issue inherit?
```

Security review is required even when the answer is "no new security surface."
That answer should be stated so the absence was intentional.

## Security Surfaces

At minimum, review these surfaces:

- authentication,
- authorization,
- control route access,
- MCP tools,
- secret handling,
- descriptor redaction,
- logs and event payloads,
- network exposure,
- Docker/private-network assumptions,
- runtime mutation,
- activity execution,
- data migration,
- persistence and rollback,
- dependency or image trust,
- denial-of-service risk,
- request forwarding behavior,
- and cross-runtime boundaries.

## Default Security Posture

Use these defaults unless a roadmap issue explicitly decides otherwise:

- mutation requires authentication,
- read-only mode is explicit,
- secrets are never returned,
- logs are bounded,
- descriptors are redacted,
- private Docker networking is not security,
- MCP mutation tools are separated from read-only tools,
- destructive activity requires approval,
- external network exposure must be named in docs/PRs,
- and package examples should model safe defaults.

## PR Security Note

Every non-trivial PR should include a security note in the decision log:

```text
Security

- New surfaces:
  ...
- Auth/authz:
  ...
- Secrets/redaction:
  ...
- Network exposure:
  ...
- Mutation/destructive behavior:
  ...
- Tests:
  ...
- Residual risk:
  ...
```

For small PRs, this can be short:

```text
Security: no runtime, network, auth, secret, or mutation behavior changed.
```

## Review Checklist

Before merging, ask:

- Does this PR expose a new route, port, tool, descriptor, log, event, or
  runtime operation?
- Can the new surface mutate state?
- Who is allowed to call it?
- How is that enforced?
- Are secrets redacted everywhere?
- Are logs bounded and safe?
- Does this rely on Docker/private network isolation as the only protection?
- Could an MCP tool do something surprising or destructive?
- Can request forwarding leak headers, tokens, or bodies?
- Are errors safe to show?
- Are dependencies/images trusted or pinned enough for the current phase?
- What should the next issue know?

## Consequences

- PRs may become slightly more verbose.
- Security assumptions become visible.
- Dangerous shortcuts should be caught earlier.
- Future UI/MCP/control-plane work has a consistent security trail.

## Non-Goals

- This ADR does not make the package production-secure by declaration.
- This ADR does not replace professional security review for production use.
- This ADR does not require every example to implement enterprise-grade
  security, but insecure shortcuts must be clearly marked as development-only.

