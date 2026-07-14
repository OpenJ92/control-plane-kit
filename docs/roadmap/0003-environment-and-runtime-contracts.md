# Roadmap 0003: Environment And Runtime Contracts

Status: In progress
Depends on: Roadmap 0001, Roadmap 0002
Parent issue: OpenJ92/control-plane-kit#19
Roadmap branch: `roadmap/0003-environment-runtime-contracts`

## Motivation

Sockets explain how nodes connect. They do not fully explain what can be
inspected or changed inside a running node.

The contract layer gives each node a typed, safe, introspectable control
surface. It also gives application developers a clean way to opt into live
mutation without making application code know about the whole control plane.

## Goal

Implement the contract model:

```text
ControlVariable
  describes a typed configurable value.

EnvironmentContract
  declares startup/runtime configuration values.

RuntimeContract
  declares mutable process state that is not necessarily env-backed.
```

The core law is:

```text
Access is always lookup.
```

## Non-Goals

- Do not build a secret manager.
- Do not require all application code to import this package.
- Do not expose mutation routes before auth/redaction behavior is tested.
- Do not implement every possible variable type at once.

## Suggested Issue Topology

1. #46: Add control variable protocol and reload policy.
2. #47: Add concrete environment and runtime variable types.
3. #45: Add `EnvironmentContract` loading, lookup, and patching.
4. #48: Add redacted contract descriptors and graph handoff.
5. #49: Add derived resources and reload policy behavior.
6. #50: Add `RuntimeContract` for mutable runtime state.
7. #51: Add contract-backed hello/router examples and roadmap documentation.

## Target API

```python
class ApiEnvironment(EnvironmentContract):
    database_url = PostgresVariable(
        env="DATABASE_URL",
        mutable=True,
        reload_policy=ReloadPolicy.DRAIN_REQUIRED,
    )
    storage_base_url = HttpVariable(
        env="STORAGE_BASE_URL",
        mutable=True,
        reload_policy=ReloadPolicy.LIVE,
    )
    sendgrid_key = SecretVariable(
        env="SENDGRID_API_KEY",
        mutable=True,
        reload_policy=ReloadPolicy.LIVE,
    )


env = ApiEnvironment.from_process()

storage = env.get("storage_base_url")
env.apply_patch({"storage_base_url": "https://storage-v2.internal"})
```

Derived resource target:

```python
engine = env.derived(
    name="database_engine",
    from_var="database_url",
    build=lambda url: create_engine(url),
    dispose=lambda engine: engine.dispose(),
)
```

## Implementation Notes

- The class is the declaration.
- The instance is the runtime holder.
- `from_process()` reads `os.environ` once.
- Runtime mutation updates the holder, not `os.environ`.
- Secret descriptors must never include raw values.
- Variable validation should own local shape; contracts should own
  cross-variable invariants.
- If code caches values outside the contract, it owns invalidation.

## Derived Resource Policy

Derived resources are contract-owned caches produced from one or more declared
variables. They preserve the core law that access is always lookup: application
code asks the contract for the current value or current derived resource instead
of keeping an untracked cache.

A patch only rebuilds derived resources automatically when all touched variables
use reload policies declared safe for automatic rebuild, currently `live` and
`custom-handler` by default. Values with `drain-required`, `restart-required`,
or `immutable` policy mark dependent resources stale instead of silently swapping
under running application code. The owner can then explicitly rebuild after the
external orchestration step has made that safe.

```python
engine = env.derived(
    name="database_engine",
    from_var="database_url",
    build=lambda contract: create_engine(contract.get("database_url")),
    dispose=lambda engine: engine.dispose(),
)

result = env.apply_patch({"database_url": "postgresql+psycopg://db-v2/app"})
assert result.stale_resources == ("database_engine",)

# After drain/cutover orchestration:
env.rebuild_derived("database_engine")
```

## Descriptor Redaction Boundary

Contract descriptors are the safe surface for control-variable state. They must
redact values by default and must never expose raw secret values.

Roadmap 0002 left a separate graph-level concern: `DeploymentGraph` descriptors
can still include environment assignments because graphs model wiring. Do not
use graph descriptors as a secret-safe control surface. Future graph descriptor
work should add explicit redacted views before real secret-bearing environment
assignments are displayed, persisted, or exposed through MCP/control routes.

## Validation

- Missing required values produce structured errors.
- Mutable values can be patched.
- Immutable values reject patches.
- Secret descriptors are redacted.
- Protocol variables validate URL/connection-string shape.
- Derived resources rebuild/dispose according to reload policy.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Package server blocks must use this contract model. Leave examples showing how a
hello server and router should migrate from hardcoded runtime state to
contracts.

