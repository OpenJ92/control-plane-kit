# External Product Ecosystem Rollout

Status: Proposed execution plan
Reference checkpoint: `pre-server-product-extraction-2026-07-20`
Reference commit: `20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec`

## Purpose

The existing `control-plane-kit` repository proved the deployment algebra,
Postgres-backed control-plane workflows, Docker interpretation, package-owned
servers, and live graph mutation in one repository. That implementation is now
an immutable reference point for a cleaner construction in parallel
repositories.

This rollout separates the deployment kernel from reusable server products
without discarding the laws, tests, examples, or operational learning that made
the current package coherent.

The central boundary is:

```text
control-plane-kit-core
  defines how products are described, admitted, instantiated, planned,
  executed, observed, and recovered

control-plane-kit-servers
  publishes reusable product descriptors and OCI images

application repositories
  publish their own descriptors and OCI images using the same contract
```

The existing `control-plane-kit` repository is not rewritten in place. It
remains available at the reference tag above while the new repositories reach
behavioral and test parity.

The initial extraction has a deliberately narrow completion boundary:

```text
complete generic control-plane-kit-core
  + core-owned CPI OCI image and external self descriptor
  + one external Hello server product
  + isomorphic unit and live-demo evidence
```

No other server product is part of the bootstrap. CoreDNS, routers, gateways,
proxies, resilience products, discovery, telemetry, and the remaining catalogue
are accumulated only after this boundary is green and reviewable.

## Decisions

### Repository names

Use these canonical names consistently:

```text
OpenJ92/control-plane-kit              frozen reference implementation
OpenJ92/control-plane-kit-core         new kernel and control-plane package
OpenJ92/control-plane-kit-servers      reusable server products and images
OpenJ92/control-plane-kit-test         optional cross-repository acceptance
```

The Python distributions and import packages are:

```text
Repository                         Distribution                    Import

control-plane-kit-core             control-plane-kit-core          control_plane_kit
control-plane-kit-servers          control-plane-kit-servers       control_plane_kit_servers
control-plane-kit-test             not initially published         no public package
```

`control-plane-kit-core` retains the `control_plane_kit` import package so the
new kernel preserves the established public vocabulary. The frozen reference
distribution and the new core distribution must not be installed together;
the reference repository is historical, not a compatibility dependency.

The dependency direction is:

```text
control-plane-kit-core
  <- control-plane-kit-servers
  <- application-owned product packages
```

Core contains the complete generic deployment pipeline without references to
Hello, CoreDNS, PgBouncer, webhook delivery, Pottery Factory, or any other
server identity. Server packages may consume core in two deliberate ways:

```text
authoring dependency
  Python constructors and validators emit the canonical product descriptor

optional runtime dependency
  a CPK-aware image uses authenticated control-route helpers
```

Neither dependency is mandatory for a non-Python or CPK-unaware application.
Core consumes admitted language-neutral descriptors; it never imports the
server catalogue or discovers products by importing arbitrary modules.

### Core self-description

`control-plane-kit-core` also publishes the runnable control-plane instance as
an immutable OCI image and publishes a companion language-neutral descriptor:

```text
ghcr.io/openj92/control-plane-kit-core/control-plane-instance@sha256:...
control-plane-instance.product.cpk.json
```

This does not make the control-plane instance a built-in case in the core
algebra. The descriptor is ordinary external product data that declares the
image, provider and requirement sockets, configuration, verification, and
capabilities of the process hosted by core. Core must not automatically import,
trust, or register its own descriptor.

The bootstrap build proves that the image and descriptor are publishable and
internally coherent. Recursive self-hosting remains later work:

```text
published core descriptor
  -> ordinary catalogue admission
    -> ordinary graph node
      -> parent CPI deploys child CPI
```

The same descriptor and image digest must be used later. Gate 9 must not invent
a second CPI product declaration or a privileged self-registration path.

#### Core operator entry surfaces

The core OCI image is not complete if it only starts internal application
services. It must host both operator-facing protocol adapters:

```text
authenticated operator HTTP API gateway
  -> shared application command/read services

authenticated MCP Streamable HTTP server
  -> the same application command/read services
```

The HTTP and MCP adapters are entrypoints, not independent owners of graph,
activity, execution, or observation truth. They must not import stores directly,
open their own transaction conventions, or implement competing projections.
Every mutating call delegates to the same authorization, approval, UnitOfWork,
idempotency, and activity-history boundaries. MCP must not provide a shortcut
around an HTTP-visible safety law.

The frozen reference already owns a `ReadOnlyMcpAdapter` tool vocabulary, but it
is intentionally transport-neutral and is not a hosted MCP server. Extraction
extends that canonical vocabulary into the hosted transport rather than creating
a parallel tool table. Mutation tools are added only where an existing
application command service and authorization law can be reused exactly.

The initial remote surfaces are declared explicitly by the self descriptor:

```text
operator-api
  authenticated HTTP API-gateway provider

operator-mcp
  authenticated MCP Streamable HTTP provider
  canonical endpoint path: /mcp
```

The implementation may serve both logical providers from one HTTP listener or
separate listeners, but the product descriptor and read model preserve their
distinct semantic contracts. The connection-protocol language must represent
MCP Streamable HTTP as a closed typed application protocol over TCP rather than
free-form metadata. Endpoint paths, authentication requirements, readiness,
and verification remain explicit.

OCI answers how the MCP process is built, distributed, and run. It does not
answer how an MCP client discovers or registers the remote server. Keep three
records distinct:

```text
OCI image reference
  deployable process bytes

control-plane-instance.product.cpk.json
  CPK product registration, sockets, requirements, and runtime contract

MCP remote registration metadata
  Streamable HTTP URL and client authentication information
```

After deployment, observations expose the selected CPI's public MCP URL, such
as `https://instance.example/mcp`. A Hub, iOS client, or operator UI may vend
that URL to an MCP client. Publishing MCP Registry `server.json` metadata is an
optional later distribution concern and must not replace CPK product admission.

Local `stdio` MCP may be supplied as a developer adapter, where the MCP client
launches the process. It is not the remotely registered production surface and
is not required for the OCI self-hosting proof.

Normative protocol references:

- [MCP transports](https://modelcontextprotocol.io/specification/draft/basic/transports)
- [Publishing remote MCP servers](https://modelcontextprotocol.io/registry/remote-servers)

### Initial workload constraint

```text
All initially supported long-running server products use immutable OCI images.
```

OCI is the first workload implementation language. It is not a claim that
every graph node is a container or that no other implementation language may
ever exist.

Distinct future product forms remain possible:

```text
ContainerServerProduct
  long-running process with provider and requirement sockets

FunctionProduct
  invocation-driven computation, including Lambda-compatible OCI images

ManagedResourceProduct
  RDS, S3, managed Redis, external SMTP, and similar provider resources

TopologyProduct
  parameterized expansion into a deployment recipe fragment
```

The first extraction implements `ContainerServerProduct`. It must not flatten
functions, managed resources, or topology products into fake servers.

### Canonical product representation

The canonical registration boundary is language-neutral, deterministic data.

```text
product.cpk.json
  -> strict decoder and validator
    -> typed ContainerServerProduct
      -> immutable ProductCatalog
```

Python dataclasses remain the implementation and optional authoring interface.
A future compiler or another language-specific builder may target the same
descriptor. This rollout does not design a custom source language or compiler.

### Product identity

Product identity is structurally closed and extension-friendly:

```text
ProductIdentity
  = namespace
  x name
  x contract revision
```

Examples:

```text
cpk-servers / coredns / 1
cpk-servers / pgbouncer / 1
pottery-factory / api / 1
control-plane-kit / instance / 1
```

The current `PackageServerProduct` enum is migration input, not the external
identity model. Unknown or duplicate identities fail closed at admission and
realization boundaries.

## Algebra

### Product definition

The general construction is:

```text
ProductDefinition[A]
  = ProductIdentity
  x ConfigurationLanguage
  x ImplementationSpecification
  x VerificationContract
  x (Configuration -> A)
```

The first concrete product is:

```text
ContainerServerProduct
  = ProductDefinition[DeployBlock]
```

Its conceptual components are:

```text
ContainerServerProduct
  = ProductIdentity
  x OciImageReference
  x ServerSpec
  x ProviderSockets
  x RequirementSockets
  x ConfigurationContract
  x CapabilityEvidence
  x VerificationContract
  x LifecyclePolicy
```

Instantiation is pure:

```text
instantiate
  : ContainerServerProduct
  x RoleId
  x ProductConfiguration
  -> DeployBlock
```

The resulting block enters the existing pipeline unchanged:

```text
ContainerServerProduct
  -> DeployBlock
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
  -> approved execution
```

### Server and connection ownership

A product owns socket declarations. A deployment graph owns socket
connections.

```text
ServerBlock
  = ServerSpec
  x OciImageImplementation
  x RoleSockets

DeploymentGraph
  = RuntimeContexts
  x DeployBlocks
  x SocketConnections
```

The descriptor says that an API requires PostgreSQL. A particular deployment
says whether that requirement is fulfilled by local Postgres, PgBouncer, RDS,
or another compatible provider.

### Runtime interpretation

The OCI image is the workload artifact. The runtime context determines where
it runs.

```text
OciImageImplementation
  |-> DockerRuntimeInterpreter
  |-> EcsRuntimeInterpreter
  |-> KubernetesRuntimeInterpreter
  |-> CloudRunRuntimeInterpreter
  `-> container-enabled EC2 interpreter
```

Managed RDS is not interpreted as an OCI image. It is a managed resource that
provides a PostgreSQL socket compatible with containerized consumers such as
PgBouncer or an application API.

Lambda-compatible OCI images remain possible, but Lambda is invocation-driven
rather than a continuously listening server. Lambda receives a distinct
runtime implementation and invocation contract when that vertical is built.

### Capability implementation

CPK-authored images may implement the standard authenticated control-route
protocol directly:

```text
/__control/status
/__control/capabilities
/__control/configuration
/__control/runtime
```

The descriptor advertises only route sets and mutations the image actually
implements. Application adoption is progressive: an arbitrary user image can
begin with sockets, startup configuration, runtime lifecycle, and verification
without importing CPK or exposing control routes.

An upstream image is not wrapped merely to make its interface look uniform.
Capability evidence may instead be implemented by:

```text
application probe
authenticated CPK control route
runtime lifecycle interpreter
typed native-protocol adapter
```

For example, PgBouncer behaves as a PostgreSQL server to clients and as a
PostgreSQL client to its upstream database:

```text
RDS.postgres
  -> PgBouncer.upstream_database

PgBouncer.pooled_database
  -> API.database
```

Its product owns one PostgreSQL requirement socket and one PostgreSQL provider
socket over TCP. Public configuration renders the upstream endpoint into an
immutable `pgbouncer.ini` artifact; credentials arrive through secret
references. Reload, pause, resume, and administrative observation may later be
interpreted through a bounded PgBouncer administration-protocol adapter. They
do not require decorative HTTP routes or access to mutable graph truth.

The product declaration is the trusted semantic interpretation of an opaque
image:

```text
OCI image provides behavior.
Product descriptor declares behavior.
Verification contract tests the declaration.
Product catalogue records admission.
Runtime context determines where behavior runs.
Socket connections supply concrete dependencies.
```

## Repository Topology

### Frozen reference

```text
control-plane-kit/
  existing source
  existing tests
  existing roadmaps and learning
  SERVER_PRODUCT_ROLLOUT.md
```

The repository remains useful for:

- behavioral comparison;
- source and test migration;
- historical decision logs;
- live scenario definitions;
- rollback to the pre-extraction tag;
- and detecting capabilities accidentally lost during extraction.

No new server product is added to the frozen package after this plan is
accepted. Security fixes may still be applied deliberately if the reference is
running anywhere.

### Core repository

The target begins approximately as:

```text
control-plane-kit-core/
  AGENTS.md
  README.md
  pyproject.toml
  Dockerfile
  test.sh

  control_plane_kit/
    core/
      algebra.py
      products.py
      configuration.py
      environment.py
      verification.py
      topology/
      planning/

    application/
      deploy/

    operations/
      activity/
      approval/
      execution/
      recovery/
      reads/

    interpreters/
      docker/
      http/
      probes/
      configuration/

    stores/
      postgres/

    entrypoints/
      cli.py

  tests/
    architecture/
    fixtures/
```

The final paths must follow evidence from the current inventory rather than a
single mechanical move. The ownership laws are:

```text
Core owns graph and product languages.
Operations own durable control-plane truth and transaction boundaries.
Interpreters perform external effects.
Entrypoints compose processes.
```

Core does not own reusable server identities, product-specific applications,
or a default server catalogue.

### Server repository

Each server is one self-contained product vertical. Its descriptor,
configuration language, internal domain, durable operations, protocol
adapters, process source, Dockerfile, templates, and tests remain under one
owning directory:

```text
control-plane-kit-servers/
  AGENTS.md
  README.md
  pyproject.toml
  Dockerfile
  test.sh

  control_plane_kit_servers/
    catalog.py

    products/
      hello/
        product.cpk.json
        declaration.py
        language.py
        verification.py
        image/
          Dockerfile
          src/
        tests/

      coredns/
        product.cpk.json
        declaration.py
        language.py
        verification.py
        templates/
          Corefile.j2
          zone.j2
        tests/

      pgbouncer/
        product.cpk.json
        declaration.py
        language.py
        verification.py
        adapters/
          admin.py
        image/
          Dockerfile
        templates/
          pgbouncer.ini.j2
        tests/

      webhook_delivery/
        product.cpk.json
        declaration.py
        language.py
        verification.py
        domain/
          commands.py
          events.py
          state.py
          codec.py
        operations/
          service.py
          stores.py
          unit_of_work.py
        adapters/
          outbound_http.py
        image/
          Dockerfile
          app.py
          bootstrap.py
        tests/
```

The descriptor and implementation are separated inside one product directory,
not separated from the product itself:

```text
products/<product>/product.cpk.json
products/<product>/declaration.py
products/<product>/language.py
products/<product>/verification.py
products/<product>/image/
products/<product>/templates/
products/<product>/domain/        # only when the product owns a real language
products/<product>/operations/    # only when the product owns durable truth
products/<product>/adapters/      # only for product-specific effects
products/<product>/tests/
```

This is preferred to global `descriptors/` and `implementations/` folders,
which would make one product difficult to inspect and easier to change
inconsistently.

An integration backed by an upstream image may omit `image/`. It still pins an
admitted image digest and owns configuration, sockets, capability evidence,
and verification. A CPK-authored server includes its source and Dockerfile.

The package-level catalogue only assembles completed product declarations:

```python
from .products.coredns import COREDNS_PRODUCT
from .products.pgbouncer import PGBOUNCER_PRODUCT
from .products.webhook_delivery import WEBHOOK_DELIVERY_PRODUCT

PRODUCTS = (
    COREDNS_PRODUCT,
    PGBOUNCER_PRODUCT,
    WEBHOOK_DELIVERY_PRODUCT,
)
```

Shared support is extracted only after at least two products demonstrate a
genuine common language or interpreter. Physical similarity alone is not
enough to split one product across global `domains`, `operations`,
`interpreters`, or `entrypoints` folders.

The local ownership law is:

```text
one server product
  -> one owning directory
    -> descriptor
    -> typed declaration
    -> configuration and verification
    -> optional internal language and operations
    -> optional product-specific adapters
    -> image source or pinned upstream image
    -> complete tests
```

### Cross-repository test repository

The optional system-test repository begins only when tests genuinely require
released or checked-out artifacts from both repositories:

```text
control-plane-kit-test/
  AGENTS.md
  README.md
  Dockerfile
  test.sh

  scenarios/
    catalogue_admission/
    heterogeneous_graph/
    router_switch/
    pgbouncer_rds_shape/
    recursive_cpi/

  fixtures/
  expected/
```

OCI removes source-level coupling, but it does not remove integration risk.
Cross-repository tests still prove:

- descriptor schema compatibility;
- catalogue admission;
- image and descriptor digest agreement;
- sockets and runtime materialization;
- capability truth;
- live readiness and verification;
- cleanup and retained-resource behavior;
- and end-to-end planning and execution.

Initially, `control-plane-kit-servers` may run these tests against a pinned core
wheel. Create `control-plane-kit-test` when the same acceptance corpus must gate
both repositories, when CPI is introduced, or when cloud interpreters require a
neutral owner.

## Product Publication

### OCI images

CPK-authored images are published publicly to GHCR:

```text
ghcr.io/openj92/control-plane-kit-servers/hello
ghcr.io/openj92/control-plane-kit-servers/webhook-delivery
ghcr.io/openj92/control-plane-kit-servers/pgbouncer
ghcr.io/openj92/control-plane-instance/control-plane-instance
```

The exact registry naming may be flattened if GHCR package constraints require
it, but product identity remains independent from repository naming.

Release laws:

- production descriptors pin image digests;
- human-readable tags are aliases, never execution truth;
- development tags require explicit development policy;
- Dockerfiles use pinned bases where practical;
- builds produce provenance and an SBOM when the release workflow supports it;
- registry credentials are secret references, never descriptor values;
- and no graph-provided Dockerfile is built or executed automatically.

An initial two-step publication is acceptable:

```text
1. merge image source and build candidate image;
2. record resulting digest in product.cpk.json through a reviewed PR;
3. publish the immutable descriptor and catalogue index.
```

Automated descriptor generation may come later. This rollout does not require
a product compiler.

### Product descriptors

The first descriptor publication mechanism is deliberately simple:

```text
GitHub release or static HTTPS
  catalog.json
  products/<identity>/<revision>/product.cpk.json
```

`catalog.json` is an index, not execution truth. Each entry includes the
expected descriptor digest. CPI stores the canonical descriptor snapshot after
admission rather than trusting a mutable URL forever.

A dedicated product registry may later add search, signatures, deprecation,
compatibility, and vulnerability information without changing the product
descriptor algebra.

## How Products Reach The iOS App

The iOS app does not clone Git repositories, download OCI images, import
Python, or speak directly to Docker.

```text
developer
  -> publishes OCI image
  -> publishes product descriptor

operator in iOS
  -> asks selected CPI to admit descriptor

CPI
  -> fetches bounded descriptor
  -> validates schema and digest
  -> records admission request
  -> obtains required approval
  -> stores immutable admitted product revision

iOS graph editor
  -> reads admitted product catalogue from CPI
  -> renders configuration and sockets
  -> instantiates product in desired graph

runtime interpreter
  -> pulls pinned OCI image when approved deployment executes
```

Initial API shape:

```text
POST /catalog/admission-requests
GET  /catalog/admission-requests/{request_id}
POST /catalog/admission-requests/{request_id}/approvals
GET  /catalog/products
GET  /catalog/products/{namespace}/{name}/{revision}
```

An admission command contains either bounded descriptor data or a descriptor
URL plus expected digest. It never contains image registry credentials or
application secrets.

The admitted record is conceptually:

```text
AdmittedProductRevision
  = ProductIdentity
  x DescriptorDigest
  x CanonicalDescriptor
  x ImageDigest
  x Provenance
  x AdmissionDecision
  x AdmittedAt
```

The iOS editor uses the descriptor to render:

- product identity and maturity;
- runtime compatibility;
- configuration fields;
- provider and requirement sockets;
- capabilities;
- verification behavior;
- lifecycle and retention warnings;
- image provenance;
- and unsupported operations.

Registration and instantiation are separate:

```text
register product type
  -> admit one trusted product revision to CPI catalogue

instantiate product
  -> create one configured DeployBlock in a desired graph
```

## Secret And Supply-Chain Boundaries

Product descriptors may name secret-reference slots but never secret values.

```json
{
  "image": {
    "repository": "private.example.com/orders-api",
    "digest": "sha256:...",
    "credential_reference": "secret://registries/private-example"
  }
}
```

The reference is durable. The credential is resolved by the runtime authority:

```text
Docker credential store
AWS IAM and ECR permissions
Kubernetes imagePullSecret
cloud secret manager
```

Admission is a security-sensitive control-plane command. It requires:

- authenticated actor identity;
- explicit authorization;
- bounded descriptor retrieval;
- digest verification;
- allowed-registry policy;
- closed descriptor variants;
- capability review;
- immutable activity history;
- approval where policy requires it;
- and no imports or code execution selected by descriptor strings.

Installing a Python package or runtime adapter is a separate trusted operator
action. Receiving a graph or product descriptor can never install executable
code into the CPI process.

## Recursive Control-Plane Instances

The CPI uses the same external product law:

```text
CONTROL_PLANE_INSTANCE_SERVER
  : ContainerServerProduct

CONTROL_PLANE_INSTANCE_STACK
  : TopologyProduct
```

The server product describes the CPI OCI image and its HTTP, Postgres, runtime
authority, configuration, secret, capability, and verification contracts. The
stack product composes public entry, Auth, CPI, Postgres, runtime authority,
and socket connections when that higher-level product language exists.

Catalogue composition occurs at CPI startup:

```text
ProductCatalog.empty()
  + control-plane-kit-servers base catalogue
  + operator-admitted products
  + admitted core CPI descriptor, when explicitly selected
```

The core package does not import or automatically register the server package.
The CPI entrypoint is the composition root. The self descriptor is not in the
catalogue merely because its image hosts the running process; an operator or
parent CPI admits it through the ordinary authenticated workflow.

Recursion is graph recursion:

```text
parent CPI graph
  contains child CPI product instance

child CPI
  owns independent Postgres, graph, activity, approval, and observation truth
  starts with its own admitted catalogue
  may instantiate another CPI product
```

The parent owns child lifecycle evidence, endpoint observation, and connection
provenance. It does not own the child's graph or activity database. Direct
navigation uses the child's advertised public endpoint; recursive proxying is
not required.

## Testing Policy

The extraction must preserve or strengthen the current testing standard.

### Universal laws

All repositories use Docker-first validation and expose:

```bash
./test.sh
```

Tests must not:

- weaken assertions to accommodate migration;
- introduce unjustified skips;
- replace application behavior with mocks where a bounded real fixture exists;
- preserve competing legacy and canonical implementations;
- infer capabilities from free-form metadata;
- or accept unpinned production images.

Every broad vertical includes architecture, security, data-engineering,
ownership, retained-data, and test-integrity review.

### Isomorphic reference parity

The frozen reference suite is an executable specification. Migration requires
a structure-preserving map from every reference test and live demonstration to
the new repository topology.

Conceptually:

```text
ReferenceBehavior
  -- ownership and representation map -->
CoreBehavior x ServerProductBehavior x SystemBehavior
```

For a normalized scenario input `x`, the parity law is:

```text
normalize(reference(x))
  = normalize(extracted(core_part(x), product_part(x)))
```

Normalization may remove incidental timestamps, generated UUIDs, allocated
ports, and container names. It must not remove product identity, graph shape,
activity order, failure classification, capability evidence, HTTP response
semantics, observations, retained-resource outcomes, or cleanup evidence.

This is semantic isomorphism, not a requirement to preserve filenames or test
class organization. One reference test may become several focused successor
tests, and several repetitive reference tests may become one stronger
parameterized law. No assertion or negative case may disappear without an
explicit reviewed supersession record.

Maintain a machine-readable parity manifest with entries shaped approximately
as:

```json
{
  "reference": "tests/test_graph_diff.py::GraphDiffTests::test_socket_change",
  "law": "topology.diff.socket-change",
  "owner": "control-plane-kit-core",
  "successors": [
    "tests/test_graph_diff.py::test_socket_change_is_structural"
  ],
  "status": "passing",
  "evidence": "core-ci-run-or-commit"
}
```

Allowed owners are:

```text
control-plane-kit-core
control-plane-kit-servers:<product>
control-plane-kit-test:<scenario>
```

The harness fails when a reference test is unmapped, a successor is absent or
not passing, a mapping weakens the original assertion, or a removed behavior
lacks an approved supersession rationale.

Parity is evaluated per transferred ownership boundary. The bootstrap cannot
claim completion until every core-owned reference test and every Hello-owned
reference test has an isomorphic passing successor. Tests belonging to products
that have not yet transferred remain explicitly `deferred` against the frozen
reference; they are neither deleted nor counted as migrated. A later product
transfer changes its entries from `deferred` to `required` and cannot complete
until their successors pass.

Pure and unit-level parity runs in the owning repository. Cross-boundary
differential tests may run the frozen reference and extracted system in
separate Docker containers, collect canonical result descriptors, normalize
only approved incidental fields, and compare them from the neutral harness.

### Live demonstration parity

Every reference live script and documented demo receives an acceptance entry:

```text
reference demo
new owning repository or system scenario
required OCI images and descriptor digests
observable requests and responses
durable events and observations
cleanup and retained-resource result
status and evidence
```

At minimum, preserve the observable behavior of the existing:

- generated Hello topology;
- authenticated router switch;
- transport and TCP switch proofs;
- configuration and secret materialization proofs;
- verification observation proof;
- webhook delivery proof;
- service discovery and telemetry proof;
- heterogeneous service infrastructure proof;
- and complete Gate F deployment transition.

A rewritten script is not sufficient evidence. The new demo must execute
through the extracted public product descriptor, pinned OCI image, core
pipeline, real runtime interpreter, observations, and cleanup path.

### Core tests

`control-plane-kit-core` retains or reconstructs:

- algebra and construction laws;
- exhaustive descriptor codecs;
- graph validation and diff laws;
- planning and execution scenarios;
- Postgres UnitOfWork and concurrency tests;
- saga, recovery, and uncertainty tests;
- Docker ownership, retention, and cleanup tests;
- HTTP, probe, configuration, and secret interpreter tests;
- package DAG and AST architecture policies;
- base-wheel and optional-dependency tests;
- CPI image build, external self-descriptor, and import-isolation tests;
- authenticated HTTP API-gateway and hosted MCP Streamable HTTP contract tests;
- HTTP/MCP authorization, projection, command, and transaction parity tests;
- invalid MCP origin, authentication, payload, method, and tool tests;
- one external fixture-product proof;
- and existing DeploymentProgram acceptance.

Core tests must not import `control_plane_kit_servers` except in an explicitly
separate external-extension fixture or integration job.

### Server-product tests

Every server product proves:

```text
descriptor
  -> strict decode
  -> typed product
  -> instantiate
  -> graph encode/decode
  -> plan
  -> runtime material
  -> live OCI behavior
  -> observation
  -> cleanup
```

Required evidence includes:

- descriptor determinism and digest stability;
- invalid configuration and socket matrices;
- image build and pinned identity;
- advertised capability implementation evidence;
- process start versus readiness distinction;
- semantic verification, not only open-port checks;
- secret exclusion;
- read-only configuration where applicable;
- ownership and retained-data behavior;
- replay and cleanup;
- architecture/import boundaries;
- and public catalogue round trips.

### Cross-repository compatibility

Compatibility tests pin exact revisions:

```text
core wheel digest
server catalogue commit
product descriptor digest
OCI image digest
scenario expectation
```

The matrix initially tests the latest compatible core and server heads. Before
the first release, no compatibility-version machinery is required; the current
language is updated in place. Once independently released versions exist, an
explicit compatibility policy must precede support for multiple schema
generations.

## Rollout Topology

The extraction is a roadmap with mandatory gates, not one uninterrupted move.

There are two execution horizons:

```text
Bootstrap extraction
  Gate 0 -> Gate 1 -> Gate 2 -> mandatory stop

Post-bootstrap accumulation
  Gate 3 onward, started only by a later operator decision
```

The bootstrap exists to prove the architecture with the smallest nontrivial
external product. It must not opportunistically transfer a second server.

### Gate 0: Freeze And Recovery Point - Complete

Evidence:

- Roadmap 0008 merged to `develop` through PR #228;
- `develop` promoted to `main` through PR #592;
- complete Docker/Postgres and compile validation passed;
- annotated tag `pre-server-product-extraction-2026-07-20` pushed;
- reference repository remains available.

### Gate 1: Core Product Extension Language

Deliver in `control-plane-kit-core`:

1. inventory reference tests, laws, and live demonstrations;
2. establish the machine-readable parity manifest and harness;
3. namespaced `ProductIdentity`;
4. language-neutral `ContainerServerProduct` descriptor;
5. strict codec and bounded validation;
6. pure product instantiation into `DeployBlock`;
7. immutable `ProductCatalog` composition;
8. duplicate and unknown identity rejection;
9. runtime and capability implementation identities;
10. admitted descriptor digest semantics;
11. runnable CPI OCI image and pinned publication workflow;
12. external `control-plane-instance.product.cpk.json` self descriptor;
13. authenticated operator HTTP API-gateway entrypoint;
14. hosted MCP Streamable HTTP entrypoint and closed protocol identity;
15. shared authorization, application-service, projection, and transaction
    behavior across HTTP and MCP;
16. image/descriptor coherence and base live-health proof;
17. external fixture package proof;
18. architecture, security, and test-parity review.

Stop before moving a real product until an external descriptor can pass the
entire pure pipeline and produce pinned runtime material.

Gate 1 is completed before `control-plane-kit-servers` receives product code.
An intentionally tiny fixture may prove the extension boundary, but it is not a
catalogue product and must not introduce Hello or another server identity into
core.

The core-owned CPI descriptor is the sole deliberate product declaration in the
core repository. It lives at a publication boundary rather than inside the
generic graph, planning, execution, or catalogue language. Architecture tests
must prove that importing core does not load the descriptor, process entrypoint,
HTTP stack, or image implementation.

### Gate 2: Server Repository And Hello Proof

Deliver in `control-plane-kit-servers`:

1. repository scaffold and AGENTS policy;
2. Docker-first test harness;
3. public catalogue assembly;
4. Hello product descriptor;
5. Hello OCI image source and Dockerfile;
6. public GHCR image workflow;
7. live Docker deployment through core;
8. descriptor publication proof;
9. ownership and cleanup evidence;
10. cross-repository handoff documentation.

This is the first complete external product vertical.

It is also the complete bootstrap server scope. At Gate 2 closeout:

```text
control-plane-kit-core
  contains the full generic pipeline plus one external self-description
  for its runnable CPI image; the pipeline contains no product identities

control-plane-kit-servers
  contains exactly one migrated product: Hello
```

Run the complete core and Hello parity manifests, reproduce every transferred
live demonstration, verify cleanup, and stop. Do not begin CoreDNS or any other
catalogue transfer as part of the bootstrap run.

### Gate 3: Post-Bootstrap Representative Integration

This begins a separate accumulation roadmap after the bootstrap has been
reviewed and accepted.

Move CoreDNS first because it exercises:

- an upstream OCI image;
- TCP and UDP providers;
- product-specific configuration;
- immutable artifacts;
- discovery projection;
- readiness and semantic verification;
- ownership and cleanup;
- and no package-owned application process.

The old CoreDNS implementation remains only in the frozen reference. No
compatibility facade is added to core.

### Gate 4: Teaching HTTP Product Family

Move in reviewable families:

```text
proxy
routers and multiplexer
balancer and rate limiter
retry, timeout, circuit breaker, and bulkhead
logger, observer, cache, fault injector, and load generator
```

Replace generated command strings with published OCI images. Preserve exact
capability, socket, verification, and scenario behavior.

### Gate 5: Domain-Backed Products

Move each product-specific interior into its owning product directory:

```text
service discovery
webhook delivery
idempotency gateway
test auth gateway
OpenTelemetry integration
TCP switch
```

Each substantial product preserves:

```text
products/<product>/
  product.cpk.json
  declaration.py
  domain/
  operations/
  adapters/
  image/
  tests/
```

Not every directory is required for every product. A directory exists only
when the product owns that concern. Core retains only behavior genuinely
required by generic control-plane operations, and the server repository does
not create global product-internal domain or operation drawers.

### Gate 6: Remaining Product Catalogue

Resume the unfinished Gate G products in the external repository:

```text
PgBouncer
Redis-compatible cache
broker language and NATS/RabbitMQ/Kafka products
MinIO/S3-compatible storage
SMTP
API-gateway and service-edge topology recipes
heterogeneous scenario corpus
```

No new product is implemented first in the frozen reference repository.

### Gate 7: Pottery Factory External Registration

Prove that an application outside both CPK repositories can participate:

```text
Pottery Factory API image
  + pottery-factory product descriptor
  + provider and requirement sockets
  + verification contract
  -> admitted product
  -> graph instance
  -> Docker deployment
```

This is the decisive extensibility test. Pottery application code need not
import CPK. Its deployment repository owns the descriptor.

### Gate 8: Catalogue Admission And iOS Read Model

Implement in the CPI vertical:

1. product admission commands and records;
2. descriptor retrieval and digest verification;
3. authorization and approval;
4. immutable admitted revisions;
5. product catalogue read service;
6. API, CLI, MCP, and iOS projections;
7. instantiation from admitted product identity;
8. secret-reference selection;
9. provenance and capability display;
10. audit and recovery behavior.

The iOS app speaks only to the selected CPI.

### Gate 9: Recursive CPI Product

Admit the already-published core CPI descriptor as an ordinary external server
product, compose the selected base catalogue, and prove:

```text
bootstrap CPI
  -> deploy child CPI product and its private stores
  -> observe child public endpoint
  -> authenticate directly to child
  -> child admits or inherits configured catalogue
  -> child can deploy another CPI
```

Stop if recursion creates shared graph truth, shared activity history, or an
implicit recursive proxy.

## Migration And Rollback

### Core history

Create `control-plane-kit-core` from the frozen reference history rather than a
source-only copy. Push the reference tag and relevant history to the new remote,
then remove product-specific code in topological PRs. This preserves blame,
decisions, and test ancestry while leaving the original remote untouched.

### Server history

Create `control-plane-kit-servers` as a new repository. Move products by
coherent vertical, carrying their tests, examples, and decision history in PR
documentation. Do not leave duplicate canonical implementations in core.

Initially, move only Hello. Do not scaffold empty directories or premature
catalogue registrations for later products. Each later product enters through
its own topologically ordered transfer issue after the bootstrap is accepted.

### Parity ledger

Maintain a machine-readable and human-readable migration ledger:

```text
old module
new owner
source moved
reference tests inventoried
successor tests passing
assertion parity reviewed
descriptor published
image published
reference demos inventoried
live behavior reproduced
cleanup evidence reproduced
old core copy removed
```

A product is not migrated merely because its Python source was copied. It is
migrated only when every owned reference test has a passing isomorphic
successor and every owned live demonstration has equivalent observable and
cleanup evidence.

### Rollback

At every gate:

- the frozen tag remains runnable;
- core and server heads are pinned in integration tests;
- image digests remain immutable;
- descriptor releases remain immutable;
- failed extraction work is reverted in the new repositories, not repaired by
  mutating the frozen reference architecture;
- and no old repository is deleted.

## Immediate Issue Topology

The bootstrap issue topology was created in the frozen coordination repository
on 2026-07-20 because the target repositories do not exist yet. Root issue
[#594](https://github.com/OpenJ92/control-plane-kit/issues/594) owns seven
mandatory milestone parents and 67 implementation, review, and closeout
children:

```text
#595 reference parity
  -> #596 core repository genesis and generic migration
    -> #597 external OCI product language
      -> #598 CPI image, HTTP API, and MCP
        -> #599 core release-candidate mandatory stop
          -> #600 server repository and Hello-only transfer
            -> #601 cross-repository bootstrap acceptance
```

Each GitHub parent contains its exact dependency graph, acceptance laws, test
evidence, stop conditions, and attached sub-issues. The child ranges are:

```text
#595 -> #602-#609
#596 -> #610-#619
#597 -> #620-#629
#598 -> #630-#642
#599 -> #643-#648
#600 -> #649-#658
#601 -> #659-#668
```

Issue ownership migrates only after the target repository exists:

```text
#610 creates control-plane-kit-core
  -> core implementation issues may transfer there

#649 creates control-plane-kit-servers after #648 operator approval
  -> server and Hello implementation issues may transfer there

#659 decides whether cross-repository evidence justifies control-plane-kit-test
  -> no empty acceptance repository is created prematurely
```

The GitHub sub-issue relationships are canonical. Textual predecessor lists in
each child explain cross-parent edges. If transfer changes issue numbers, update
the parent topology and this section in the same coordination PR.

The core topology completes and stops at #648 before #649 creates the server
repository. The Hello live proof in #658 is the first product-level
cross-repository gate. Full system acceptance begins at #659.

Post-bootstrap Gates 3-9 are preserved separately under deferred parent
[#669](https://github.com/OpenJ92/control-plane-kit/issues/669):

```text
#668 operator acceptance
  -> #669 deferred accumulation
    -> #670 CoreDNS
      -> #671 teaching HTTP products
        -> #672 domain-backed products
          -> #673 remaining catalogue and recipes
            -> #674 Pottery Factory registration
              -> #675 catalogue admission and iOS
                -> #676 recursive CPI
```

These are future roadmap parents, not bootstrap implementation issues. Each
must be rehydrated and decomposed after bootstrap learning before execution.

CoreDNS and every remaining product belong to a new post-bootstrap issue
topology. They are not children of `SERVER-BOOTSTRAP`, and work on them must not
begin merely because their frozen implementations already exist.

Server architecture policy must reject:

- product-specific domain modules outside their owning product directory;
- product-specific stores or services outside their owning product directory;
- product-specific runtime adapters hidden in generic interpreter packages;
- product applications imported eagerly by the public catalogue;
- imports from one product's internals into another product;
- and a shared helper extraction supported by only one product.

The public catalogue may import each product's lightweight declaration
entrance. It must not import image applications, stores, network clients, or
process bootstrap.

## Definition Of Success

The bootstrap extraction succeeds when:

```text
control-plane-kit-core
  owns the complete generic pipeline without built-in server-product identity
  and publishes its CPI image plus external self descriptor, authenticated HTTP
  API gateway, and hosted MCP Streamable HTTP endpoint

control-plane-kit-servers
  owns one complete Hello product descriptor and pinned OCI image

control-plane-kit-test
  proves core and Hello isomorphic test and live-demo parity
```

This is a real release boundary, not completion of the eventual server
catalogue. Subsequent products accumulate one vertical at a time under the same
parity law.

The broader ecosystem succeeds when:

```text
control-plane-kit-core
  can describe and execute products without knowing product identities

control-plane-kit-servers
  can publish independently versioned descriptors and OCI images

Pottery Factory
  can register its own product without modifying either CPK repository

CPI and iOS
  can admit, inspect, instantiate, connect, approve, and execute products
  through durable authenticated workflows
```

The mathematical structure remains:

```text
Objects
  product descriptors, admitted revisions, blocks, runtimes, sockets, graphs,
  plans, effects, and observations

Morphisms
  decode, validate, admit, instantiate, connect, diff, plan, approve, execute,
  observe, compensate, and project

Laws
  identity uniqueness, deterministic descriptors, explicit compatibility,
  immutable admission, pinned execution, truthful capabilities, secret
  exclusion, transaction ownership, effect separation, replay safety,
  retained-data safety, and independent child-instance truth
```

The split is complete only when those laws are executable across repository
boundaries with the same rigor as the frozen reference implementation.

The mechanical closeout conditions are:

```text
unmapped reference tests                  = 0
missing or failing successor tests        = 0
unreviewed weakened assertions            = 0
unmapped reference live demonstrations    = 0
failed required live demonstrations       = 0
unexpected owned runtime residue          = 0
```
