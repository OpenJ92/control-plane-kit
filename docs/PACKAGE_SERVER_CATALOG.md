# Package Server Catalogue

This catalogue records implementation truth for package-owned servers. It is
generated from explicit typed contracts, never from free-form block metadata.

```text
ProductDeclaration
  = PackageServerProduct
  x ProductMaturity
  x DeployBlock
  x tuple[ExecutableCapability, ...]
```

The declaration language is importable without runnable server extras:

```python
from control_plane_kit.products.servers import (
    ProductCatalog,
    ProductDeclaration,
)
```

Webhook delivery, the test-only auth gateway, and CoreDNS establish the
canonical product exterior. Runnable FastAPI applications, stores, HTTP clients,
and process bootstrap belong to operations, interpreters, or entrypoints rather
than this declaration surface. Teaching-server relocations may proceed
incrementally, but new products enter directly through this package.

`BlockSpec.capabilities` contains only powers implemented by the realized
server. An in-memory teaching model does not make its Docker server mutable.
Unsupported powers resolve to `UnsupportedCapability`; they are not advertised.

| Product | Maturity | Startup/runtime binding | Advertised capability evidence |
| --- | --- | --- | --- |
| Hello | teaching | optional HTTP/Postgres dependencies through environment | application health probe `/` |
| HTTP proxy | teaching | target through environment | application health probe `/` |
| HTTP active router | teaching | active target through environment | application health probe `/` |
| HTTP multiplexer | teaching | primary and observers through environment | application health probe `/` |
| HTTP rate limiter | teaching | target through environment; quota is startup configuration | application health probe `/` |
| HTTP weighted load balancer | teaching | two targets through environment; weights are startup configuration | application health probe `/` |
| Managed HTTP router | operational package fixture | targets through environment; active target through runtime control | authenticated common-status and target route sets |

The teaching implementations remain useful for graph composition and local
Docker acceptance. They are not production infrastructure and do not claim
runtime target, observer, weight, quota, metrics, or drain behavior that their
running stdlib processes cannot execute.

Package product identity is durable:

```python
PackageServerSpec(
    role_id="edge-router",
    product=PackageServerProduct.MANAGED_HTTP_ROUTER,
    capabilities=(
        CapabilityName.HEALTH_CHECKABLE,
        CapabilityName.TARGET_MUTABLE,
        CapabilityName.SWITCHABLE,
        CapabilityName.DRAINABLE,
    ),
)
```

The default graph codec emits the `package-server` variant and the exact closed
product value. Reconstruction therefore never guesses behavior from
`metadata["behavior"]`.

## Extension Law

A future package server must:

1. add an exact `PackageServerProduct` variant;
2. construct a `PackageServerSpec` carrying that variant;
3. register one `ProductDeclaration`;
4. advertise only capabilities backed by a probe, authenticated control route,
   or runtime adapter operation;
5. declare provider and requirement sockets explicitly;
6. state whether it is a teaching implementation or an operational product
   integration; and
7. add descriptor, invalidity, security, and representative runtime evidence.

The catalogue is an implementation registry and audit surface. It is not a
second planner, executor, graph, or runtime language.
