# Trusted Identity Boundary

Status: HARDEN.AUTH.0 through HARDEN.AUTH.2 implemented foundation

## Threat Model

The public request body describes operator intent. It is not evidence of who
sent the request or what that caller may do. In particular, these request fields
are untrusted input:

```text
actor_id
actor_scopes
workspace_id
worker_id
requested_by
approved_by
```

The governing path is:

```text
opaque credential
  -> cpk-server CredentialVerifier
    -> AuthenticatedPrincipal
      -> operations PrincipalAuthorizer
        -> TrustedCommandContext
          -> existing pure policy decisions and command services
```

Credential validation belongs to the cpk-server process boundary. Core defines
only the provider-neutral result and protocols. Operations authorizes one
principal for one workspace and passes only the resulting identity and closed
scopes to command services. Credentials never enter operations, persistence,
descriptors, idempotency fingerprints, logs, errors, HTTP/MCP responses, or
runtime effects.

## Principal Kinds

`operator` identifies a human or operator-facing agent entering cpk-server.
`service` identifies an authenticated service, including a cpk-server that may
later issue bounded gateway capabilities. `worker` identifies an execution
worker. A worker is not an operator, even when issuer and subject text happen to
match.

Gateway delegation remains a separate trust boundary for #1139. A gateway is
not granted broad workspace authority. Its future capability must be bound to a
cpk-server issuer, one runtime-island audience, one workspace, one declared
target, one command kind, and a short lifetime.

## Authority Inventory And Migration

| Current field | Current use | Trusted source | Migration |
| --- | --- | --- | --- |
| `actor_id` | Commands and durable history | `TrustedCommandContext.actor_id` | #1102 removes it from public payload authority; durable history retains the authenticated subject id. |
| `actor_scopes` | Public command/read authorization | Workspace grant in `AuthenticatedPrincipal` | #1102 removes it from public payload parsing and derives scopes only from trusted context. |
| `workspace_id` | Selects workspace-scoped intent and records | Request intent checked against principal grants | #1102 authorizes access before loading or mutating workspace truth. Object identity alone grants nothing. |
| `worker_id` | Claim/start/execute ownership and fencing | Distinct authenticated worker/service identity plus durable claim | Keep distinct from operator identity. Recovery/fencing hardening owns stronger worker proof. |
| `requested_by` / `decided_by` / `approved_by` | Approval provenance | Authenticated subject id and trusted approval scope | Preserve as durable evidence; remove caller control over provenance. |
| registration actor fields | Product/runtime/ingress authority admission | Authenticated subject plus registration scope | #1102 derives identity and scope from trusted context. |
| read/use/revoke/execute scopes | Public adapter checks | Closed workspace grant | Keep permissions distinct; do not introduce an admin wildcard. |
| bearer/token material | HTTP/MCP authentication | cpk-server verifier only | #1101 validates and discards raw credentials before operations dispatch. |

The #1102 adapter migration leaves some legacy `actor_id`, `actor_scopes`, and
`worker_id` fields in transport payload schemas temporarily for compatibility.
They are inert: operations never parses them as identity or authority. All
durable actor and worker provenance is copied from `TrustedCommandContext`.
Removing those redundant transport fields and consolidating command context
shapes remains a compatibility task for #985.

## Permission Families

The existing closed `PolicyScope` vocabulary remains authoritative. The AUTH
foundation must not collapse these distinctions:

```text
register != read != use != revoke
plan request != plan approve != destructive approve != execute
execution operate != operator workspace authority
```

Broader permission-algebra consolidation remains #985. This issue establishes
trusted provenance for the existing scopes rather than redesigning them.

## Failure And Disclosure Laws

- Missing or invalid credentials fail before operations dispatch.
- A principal without a workspace grant cannot produce a command context for
  that workspace.
- A command context cannot add or remove scopes from its principal grant.
- Raw credentials never appear in principal equality, hashing, descriptors, or
  representations because they are not fields of the principal.
- Authentication failures are bounded and credential-free.
- HTTP and MCP must receive the same principal for the same credential.
- Every public route has one explicit closed authorization policy.
- Workspace authorization happens before store access or external effects.
- Workspace creation requires an authenticated grant for the requested
  workspace plus `hub:instance-create`; a request body cannot bootstrap its own
  grant.
- Worker lifecycle routes require a `worker` or `service` principal and
  `execution:operate`; an operator principal is not interchangeable.
- Admission requires `runtime-authority:use` when either transition graph
  references a runtime authority and `ingress-authority:use` when either graph
  contains public ingress.

## Handoff

#1101 implements strict credential extraction and one injected verifier for
HTTP and MCP. #1102 removes caller-authored authority from operations adapters,
enforces workspace grants before store access, and preserves distinct route
permissions. #1103 owns published-image/harness adoption of this foundation.
#1139 consumes the trusted command context to issue bounded gateway
capabilities; it must not forward inbound bearer tokens.
