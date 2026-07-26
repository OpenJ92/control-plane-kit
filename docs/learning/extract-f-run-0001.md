# EXTRACT.F Run 0001

## #650 Server Repository Policy

#650 installs the first repository-local operating policy in
`control-plane-kit-servers`. The work is policy and documentation only: no
product implementation, package metadata, Docker harness, descriptor catalogue,
cpk-server process, or Hello transfer enters here. In short: no product
implementation enters #650.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  python -m unittest tests.test_repository_policy
```

Before implementation, the focused policy tests failed because `AGENTS.md`,
`GIT-FLOW.md`, `docs/product-layouts.md`, and this learning document did not
exist. The product inventory check already passed, proving #649 left the
repository empty of implemented products while reserving cpk-server and Hello.

Policy now established:

- one product owns one directory;
- catalogue imports declaration values, not applications or stores;
- core never imports servers;
- server products may import pinned core contracts;
- cpk-server and Hello have different roles;
- Docker-first validation is required;
- broad Docker prune is forbidden;
- shared support requires evidence from two products or an explicit bootstrap
  exception.

Handoff:

- #651 owns Python package metadata, root imports, pinned core dependency, and
  declaration-only catalogue entrance.
- #652 owns the canonical Docker-first test harness, image lanes, digest
  capture, and cleanup audits.
- #653 owns descriptor catalogue shape and deterministic publication artifacts.

No product implementation was added in #650.

## #651 Package Metadata And Catalogue Entrance

#651 creates the first installable Python package surface for
`control-plane-kit-servers` without adding any product implementation.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  python -m unittest tests.test_package_metadata
```

Before implementation, the focused tests failed because `pyproject.toml` and
`src/control_plane_kit_servers` did not exist.

Package decisions:

- package name: `control-plane-kit-servers`;
- package version: `0.1.0`;
- pinned core dependency:
  `control-plane-kit-core @ https://github.com/OpenJ92/control-plane-kit/archive/a04631770efbf59e62b4536cc80a71d42873446d.zip#subdirectory=control-plane-kit-core`;
- root import exports only `__version__` and `load_catalogue`;
- `load_catalogue()` returns `()` until #653 defines completed descriptor
  publication;
- root import does not import `control_plane_kit_core`, FastAPI, HTTP clients,
  Docker clients, cpk-server, or Hello.

Handoff:

- #652 can now install and test a real package in Docker.
- #653 owns the descriptor catalogue shape and completed declaration loading.
- #813 and later own cpk-server process implementation.

Packaging finding:

- A first full `pip install .` attempt with a `git+https` pin failed in
  `python:3.12-slim` because `git` was not installed.
- The dependency was changed to an immutable GitHub archive URL for the same
  commit. This preserves the pin while keeping clean Docker installs light.

## #652 Docker-First Test And Image Harness

#652 adds the first canonical validation harness for
`control-plane-kit-servers`.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  python -m unittest tests.test_docker_harness
```

Before implementation, the focused tests failed because `test.sh`,
`Dockerfile.test`, harness scripts, and `.github/workflows/tests.yml` did not
exist.

Harness decisions:

- `./test.sh` is Docker-first and does not require host Python;
- `Dockerfile.test` installs the package and archive-pinned core dependency;
- `scripts/run_all_tests.py` runs unittest discovery and the product image lane;
- `scripts/product_image_lane.py` reports `no-products` while the inventory is
  empty;
- `scripts/docker_residue_audit.sh` inspects only resources with the exact
  `org.openj92.project=control-plane-kit-servers` label;
- GitHub Actions runs `./test.sh` on `main`, `develop`, and PRs targeting those
  branches.

Handoff:

- #653 can now rely on `./test.sh` for package and catalogue validation.
- Product image builds remain pending until actual product directories exist.
- cpk-server and Hello image lanes must share harness conventions without
  sharing product ownership.


## #653 Descriptor Catalogue And Publication Artifacts

#653 defines the server-product publication catalogue without implementing a
server product. The catalogue is metadata about completed products, not a second
core descriptor language and not an application bootstrap.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  sh -c 'python -m unittest tests.test_descriptor_catalogue -v'
```

Before implementation, the focused tests failed because the catalogue language,
strict declaration value, and publication helper did not exist.

Objects introduced:

```python
PublishedProductDescriptor
  = product_id
  x owner_directory
  x descriptor_path
  x descriptor_sha256
  x source_commit
  x image_ref
  x image_digest
  x status
```

Publication morphism:

```text
catalogue/products.json
  -> load_catalogue
  -> tuple[PublishedProductDescriptor, ...]
  -> publish_catalogue
  -> deterministic JSON + sha256 sidecar
```

Laws proven:

- default installed catalogue is empty and immutable;
- only completed declarations load;
- duplicates fail closed;
- unknown fields fail closed;
- descriptor/source/image digests are explicit;
- publication ordering and checksum are deterministic;
- catalogue loading does not import product implementation or process code.

Handoff:

- #813 can add cpk-server declaration material as ordinary product data once the
  wrapper exists.
- #816 must prove cpk-server image and descriptor digests before adding a
  completed catalogue declaration.
- Hello issues must follow the same catalogue path; no special built-in route is
  available.


## #813 cpk-server Process Composition

#813 creates the first product-local cpk-server wrapper without implementing
HTTP/MCP process routes or an OCI image. The package lives under
`products/cpk_server/src/control_plane_kit_servers_cpk_server`, keeping the root
server catalogue import light and keeping the product independently movable.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  sh -c 'python -m pip install . >/tmp/pip.log && \
         python -m unittest discover -s products/cpk_server/tests -v'
```

Before implementation, the installed package had core available but no
`control_plane_kit_servers_cpk_server` module and no product-local #813 law
cards.

Objects introduced:

```python
CpkServerProcessConfiguration
  = execution_enabled x control_token_configured x mode

CpkServerProcessState
  = targets x active_target x observers x graph_truth_policy

CpkServerComposition
  = configuration x CpkServerEntrypointHandoffContract x process_state
```

Composition morphism:

```text
CpkServerProcessConfiguration
  -> create_cpk_server_composition
    -> CpkServerComposition
      -> CpkServerEntrypointHandoffContract
        -> DeploymentProgramBoundary + HTTP contract + MCP contract + UoW boundary
```

Laws proven:

- product-local law cards assign the #813-owned #804 laws;
- execution-capable composition requires auth configuration;
- HTTP and MCP share the same core handoff/program boundary;
- observer mutation changes immutable process state, not graph truth;
- replacing target sets clears stale active target state;
- unknown target switches fail closed;
- root catalogue import does not import cpk-server;
- core import does not import cpk-server;
- Hello cannot satisfy cpk-server laws.

Handoff:

- #814 implements HTTP/MCP process boundaries over this composition and must not
  create another command vocabulary.
- #815 packages the process as OCI after #814.
- #816 adds product descriptor and catalogue publication only after image and
  descriptor digest evidence exists.


## #814 cpk-server HTTP And MCP Boundaries

#814 adds framework-neutral HTTP and MCP process boundaries over the #813
composition root. The purpose is to prove route/protocol shape and shared
delegation before introducing FastAPI, hosted MCP process bootstrap, or an OCI
image.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  sh -c 'python -m pip install . >/tmp/pip.log && \
         python -m unittest discover -s products/cpk_server/tests -v'
```

Before implementation, the #814 tests failed because
`CpkServerHttpProcessBoundary`, `CpkServerMcpProcessBoundary`, and the shared
application boundary did not exist.

Objects introduced:

```python
CpkServerApplicationBoundary
  = ControlPlaneServiceRole -> service.handle(request)

CpkServerHttpProcessBoundary
  = CpkServerComposition x CpkServerApplicationBoundary

CpkServerMcpProcessBoundary
  = CpkServerComposition x CpkServerApplicationBoundary

CpkServerServiceRequest
  = surface x route_id x service_role x path_parameters x payload
```

Morphism:

```text
HTTP route / MCP message
  -> core route id
    -> service role
      -> CpkServerApplicationBoundary
        -> one service object
```

Laws proven:

- HTTP read route delegates to the shared reads service;
- HTTP command route requires bearer authorization and delegates to planning;
- malformed and oversized request bodies fail before service dispatch;
- MCP `tools/call` and HTTP use the same application boundary;
- MCP `resources/read` uses the same reads service;
- missing MCP auth fails closed;
- unknown HTTP/MCP operations fail closed and do not touch services.

Deliberate hardening:

- The frozen block-control development fixture allowed unconfigured local
  mutation calls. Hosted cpk-server does not preserve a mutation-capable
  unauthenticated mode. This matches the refreshed #814 law that control-route
  mutation requires configured authentication.

Handoff:

- #815 can wrap these framework-neutral boundaries in a runnable process/image.
- #816 must keep descriptor/catalogue publication declaration-only and avoid
  importing these process modules during catalogue loading.
- #817 can use the same boundaries for live smoke evidence.


## #815 cpk-server OCI Image

#815 packages the cpk-server wrapper as a runnable OCI image while keeping
descriptor publication deferred to #816. The image host is intentionally stdlib
HTTP for this first proof; it wraps the #814 process boundaries rather than
creating another command surface.

Red proof:

```text
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  sh -c 'python -m pip install . >/tmp/pip.log && \
         python -m unittest discover -s products/cpk_server/tests -v'
```

Before implementation, the #815 tests failed because `products/cpk_server` had
no Dockerfile, no bootstrap contract, and no host-side image smoke script.

Objects introduced:

```text
products/cpk_server/Dockerfile
products/cpk_server/bootstrap.contract.json
control_plane_kit_servers_cpk_server.server
scripts/cpk_server_image_smoke.sh
```

Bootstrap law:

```text
CPK_SERVER_MODE=execution-capable
CPK_CONTROL_AUTH_CONFIGURED=true
CPK_PORT=<1..65535>
  -> CpkServerBootstrapConfiguration
    -> create_cpk_server_composition
      -> stdlib HTTP host over #814 boundaries
```

Live evidence:

- image builds from the pinned server package and archive-pinned core dependency;
- image runs as non-root `cpk`;
- missing bootstrap configuration exits nonzero;
- `/health/live` and `/health/ready` are reachable;
- unauthenticated operator read returns 401;
- authenticated HTTP read traverses the reads service;
- authenticated MCP `tools/call` traverses the planning service;
- owned container cleanup leaves the residue audit green.

Boundary decision:

- `coordination/product-inventory.json` now records cpk-server as
  `image-definition-present-not-published`;
- `catalogue/products.json` remains empty until #816;
- `product.cpk.json` remains a non-published stub.

Handoff:

- #816 must convert this image evidence into ordinary external product
  descriptor/catalogue publication with pinned image and descriptor digests.
- #817 can reuse `scripts/cpk_server_image_smoke.sh` as the base live smoke and
  add recursive-readiness handoff evidence.


## #816 cpk-server Product Descriptor

#816 published cpk-server as ordinary external product data in the server
repository. The descriptor is canonical `control-plane-kit.product` JSON emitted
by extracted core and has identity:

```text
ProductIdentity("control-plane-kit", "cpk-server", 1)
```

The descriptor declares two provider sockets:

```text
http-api : tcp x http
mcp      : tcp x mcp-streamable-http
```

and four Postgres requirement sockets for the child instance store boundaries:

```text
workplace-store
activity-history-store
observer-state-store
graph-topology-store
```

The catalogue now contains one completed declaration for `cpk-server` pointing at `ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416`. Admission
is still an explicit boundary: `load_catalogue()` reads publication metadata,
while `load_product_catalog(path, root=...)` verifies descriptor sha256, decodes
through `ProductDescriptorCodec`, checks image digest agreement, and returns a
core `ProductCatalog`.

Important implementation decision: the cpk-server Dockerfile now copies only the
runnable product source, not `product.cpk.json` or catalogue data. This prevents a
self-referential image/descriptor digest cycle.

Validation evidence added:

- descriptor round-trip through core product language;
- catalogue admission and core catalogue lookup;
- digest mismatch and unknown-field negative tests;
- HTTP/MCP endpoint contract tests through provider sockets and verification;
- architecture test proving descriptor/catalogue loading does not import process
  code;
- generated catalogue checksum proof.

Handoff to #817: use the published descriptor/image digest and the existing smoke
script to prove live HTTP/MCP reachability and recursive handoff readiness.


GHCR publication evidence:

```text
docker push ghcr.io/openj92/control-plane-kit-servers/cpk-server:extract-f-817
  -> digest sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416

docker pull ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416
  -> image is available by immutable registry digest
```

The server repository now has a reusable per-product publication lane:

```text
scripts/publish_product_image.sh <product-id> <tag>
.github/workflows/publish-product-image.yml
```

Only `cpk-server` is admitted by the script today. Future products should add
explicit support product-by-product rather than broad glob publishing.


Current GHCR visibility:

```text
https://github.com/users/OpenJ92/packages/container/package/control-plane-kit-servers%2Fcpk-server
visibility: private
```

Authenticated Docker Desktop and GitHub Actions can pull the digest. Public
unauthenticated pulls require an explicit package visibility decision.


## #817 Published cpk-server Live Smoke

#817 turned the cpk-server image proof from "local build runs" into "published
server product digest runs". The smoke now has two layers:

```text
scripts/cpk_server_image_smoke.sh
  -> build optional local image
  -> require explicit bootstrap configuration
  -> run one cpk-server process
  -> prove HTTP/MCP/auth/readiness
  -> cleanup and residue audit

scripts/cpk_server_published_image_smoke.sh
  -> docker pull GHCR digest
  -> CPK_SERVER_BUILD_IMAGE=0
  -> delegate to the same smoke
```

The live digest is:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416
```

The process now requires the four store endpoint environment bindings declared
by `product.cpk.json`:

```text
CPK_WORKPLACE_DATABASE_URL
CPK_ACTIVITY_HISTORY_DATABASE_URL
CPK_OBSERVER_STATE_DATABASE_URL
CPK_GRAPH_TOPOLOGY_DATABASE_URL
```

These values are bootstrap inputs for this wrapper smoke, not durable store
implementations. The stdlib demo process validates that they are present and
reports readiness as:

```json
{"application": "configured", "status": "ready", "stores": "configured"}
```

It deliberately does not echo endpoint URLs. Real Postgres-backed operations
remain a later operations/cpk-server integration responsibility.

Live behavior proven by the published image smoke:

- missing bootstrap configuration exits nonzero;
- image runs as non-root `cpk`;
- `/health/live` distinguishes process reachability;
- `/health/ready` distinguishes semantic bootstrap readiness;
- HTTP operator read without authorization returns 401;
- MCP command without authorization returns 401;
- authorized HTTP read traverses the reads service;
- authorized MCP `tools/call` traverses the planning service;
- authorized MCP `resources/read` traverses the reads service;
- response bodies do not leak store endpoint values;
- cleanup leaves the Docker residue audit green.

Handoff to #676:

- a parent CPK instance should register this product descriptor and receive a
  direct child HTTP/MCP endpoint contract;
- the parent must not implement recursive proxying to the child;
- entering a child means using the child public endpoint/auth boundary directly;
- #817 does not prove child deployment execution, only child process readiness
  and endpoint contract coherence.


## #992 Recursive Local Runtime Authority Delivery

#992 proves the local recursive cpk-server chain through explicit runtime
authority delivery:

```text
parent cpk-server-docker
  -> registers local Docker authority
  -> registers LOCAL_DOCKER_SOCKET_MOUNT delivery
  -> spawns child cpk-server-docker
    -> child registers its own local Docker authority and delivery
    -> child spawns another cpk-server-docker
```

The smoke is bounded by `CPK_RECURSIVE_LOCAL_CHAIN_DEPTH`, defaulting to `1` and
failing closed above `10`. The depth-2 proof passed with:

```text
CPK_RECURSIVE_LOCAL_CHAIN_DEPTH=2 sh scripts/cpk_server_recursive_activity_smoke.sh
```

Important implementation decisions:

- Docker socket access is explicit delivery material, not inferred from
  `cpk-server` identity or `CPK_RUNTIME_INTERPRETERS=docker`.
- The Docker interpreter mounts `/var/run/docker.sock` only when the
  `RuntimeEffectRequest` carries `LOCAL_DOCKER_SOCKET_MOUNT`.
- The interpreter adds the host socket group id as a supplementary group for the
  realized container, keeping cpk-server non-root.
- The recursive smoke pre-cleans only resources with CPK-owned recursive
  workspace labels, preserving unrelated and Pottery Factory Docker resources.
- Child secret material is finite delegated bootstrap material generated to the
  requested chain depth. It remains explicit secret delivery and never enters
  descriptors, graph data, runtime request descriptors, observations, read
  models, or logs.

Current published cpk-server digest used by the smoke:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:9eacda293d09953289a50adb9476a290b73a2406698ce352bb97904f27c1415b
```

Validation evidence:

- focused recursive static tests passed;
- coordinate source-of-truth check passed;
- published-image smoke passed;
- full `./test.sh` passed.


## #1005 Public Multi-Workspace Stress Harness Foundation

#1005 extends the hosted activity controller from a single-workspace smoke into
a reusable public multi-workspace harness foundation for SEEDED.STRESS:

```text
one published cpk-server-docker
  -> public HTTP/MCP workflow boundary
    -> workspace-a-router
    -> workspace-b-multiplexer
    -> workspace-c-postgres
    -> workspace-d-negative-cleanup
```

The controller now has a shared bootstrap path that creates each workspace,
imports only the seeded product descriptors needed for that workspace, registers
the local Docker runtime authority, and registers the explicit local Docker
socket delivery. The controller remains outside the application service layer:
it does not import operations stores, `PostgresUnitOfWork`, or
`DockerRuntimeInterpreter`.

Important implementation decisions:

- local Docker execution is still admitted through
  `RegisteredRuntimeAuthority` plus `RuntimeAuthorityDelivery`;
- graph runtimes carry `RuntimeAuthorityReference("local-docker")` instead of
  relying on ambient interpreter availability;
- the harness can choose a workspace through `CPK_HOSTED_ACTIVITY_WORKSPACE_ID`;
- `multi-workspace-foundation` prepares the seeded workspace set without
  pretending the later router, multiplexer, Postgres, and negative cleanup
  scenario laws have already been proven;
- cleanup now filters by the CPK workspace label key and removes only the
  known hosted/stress workspace labels, preserving unrelated resources and
  Pottery Factory containers.

Validation evidence:

- focused hosted activity controller tests passed;
- shell syntax validation for `scripts/cpk_server_hosted_activity_smoke.sh`
  passed;
- full `./test.sh` passed, including cpk-server image smoke and Docker residue
  audit.
