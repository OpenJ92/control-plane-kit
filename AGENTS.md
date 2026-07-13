# control-plane-kit Agent Guide

`control-plane-kit` is a generic Python package for describing deployable
systems as topology values. It must remain independent from any one application
or product repository.

## Branch Flow

Use this flow for normal feature work:

```text
develop
  -> codex/<issue-or-feature>
      -> PR into develop
```

Promote `develop` to `main` only after a coherent vertical is reviewed. Do not
open feature PRs directly into `main` unless the user explicitly asks for a
one-off patch.

## Validation

Run the narrowest useful validation before opening a PR. For code changes, use:

```bash
./test.sh
python3 -m compileall control_plane_kit tests
git diff --check
```

For documentation-only changes, `git diff --check` is sufficient unless the docs
include executable examples that should be run.

## Design Constraints

- Keep the package generic. Do not import application repositories or encode
  application-specific service names in core modules.
- Application code must not import this package. Applications expose ports and
  read URLs, connection strings, or TCP addresses from environment variables.
- The core model is algebraic data. Prefer product values and interpreters over
  deep inheritance trees.
- Blocks describe topology. Runtime implementations and runtime contexts
  interpret that topology into effects.
- The graph owns nodes, sockets, edges, environment assignments, runtime records,
  descriptors, and activity planning inputs. Runtime interpreters own processes,
  containers, cloud resources, and side effects.
- Control routes are protocol data first. FastAPI, ASGI, Docker, Kubernetes, or
  cloud interpreters may implement them later.
- Capabilities are advertised powers. They should be explicit and optional, not
  inferred from block class names.

## Vocabulary

Current public socket names still use the older `Role*Socket` vocabulary:

```text
RoleInputSocket
RoleOutputSocket
RoleSockets
```

The intended semantic vocabulary is:

```text
RequirementSocket: an env-backed requirement needing a provider value.
ProviderSocket: an endpoint/value exposed for other blocks to consume.
BlockSockets: the communication boundary of one block.
```

Do not perform this rename opportunistically inside unrelated issues. If we
rename it, do it as a small compatibility-aware refactor before more server block
APIs depend on the current names.

## Issue Handoff

For issue topology work, leave a short handoff comment when a child issue changes
what the next child should know. Keep handoffs concrete: files touched, decisions
made, tests added, and remaining risks.
