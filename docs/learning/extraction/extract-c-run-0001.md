# EXTRACT.C External OCI Server-Product Language - Run 0001

## Scope

EXTRACT.C defines the pure language that lets core admit external OCI server
products without importing product implementations, process entrypoints, Docker
clients, HTTP clients, MCP transports, Postgres stores, or package-owned server
catalogues.

The target pipeline is:

```text
product.cpk.json
  -> strict descriptor codec
    -> ContainerServerProduct
      -> ProductCatalog admission
        -> configured product
          -> ordinary DeployBlock
            -> DeploymentTopology
              -> DeploymentGraph
                -> GraphDiff
                  -> ActivityPlan
```

## Topology

```text
#620 -> #621 -> #622 -> #623
  -> #624 -> #625 -> #626 -> #627
    -> #628 -> #629
```

## Boundary Decision

`cpk-server` is not core.

Core owns the generic external product language. A future `cpk-server` should be
expressible as ordinary descriptor data:

```text
ProductIdentity
  x OciImageReference
  x typed sockets
  x configuration requirements
  x secret requirements
  x verification contracts
  x lifecycle policy
```

The likely implementation home is the future server-product side:

```text
control-plane-kit-servers/
  products/
    cpk_server/
      implementation
      OCI image
      product.cpk.json
      tests
```

EXTRACT.C must make that representation possible, but must not implement
`cpk-server`, register a built-in CPI product, or specialize core for recursive
deployment.

## Test Context Law

For every non-trivial child:

```text
inspect governing frozen tests and new requirements
  -> extract behavioral law cards
    -> dry-run source and architecture with those laws in view
      -> design the target interface and refine issue topology
        -> translate or write focused target tests
          -> prove focused target red
            -> implement to green
```

Use `unittest` only. Do not use skips, xfail, weakened assertions, hidden
collection, or imports of the frozen implementation to manufacture success.

## Initial Risk Register

- Product descriptors are untrusted input.
- OCI tags are human hints, not execution identity.
- Descriptor admission must not execute product code.
- Product identity must not imply imported Python modules.
- Secrets must remain references and never appear as descriptor values.
- Core must not learn package-owned server names.
- The product language must remain useful to future `cpk-server` without making
  `cpk-server` special.

## #620 Product Identity

### Law Card

- Reference identity: `EXTRACT.C.1.product-identity`
- Evidence source: rollout issue #620, `SERVER_PRODUCT_ROLLOUT.md` product
  identity section, frozen package-server enum/catalogue tests as migration
  input only.
- Observable law: external product identity is structural data, not a Python
  import, enum member, image string, or executable registry lookup.
- Object:

```text
ProductIdentity
  = namespace
  x name
  x contract_revision
```

- Expected result: valid lowercase ASCII namespace/name parts construct,
  encode, decode, sort, and round-trip deterministically.
- Negative cases: uppercase, Unicode, path fragments, shell-like punctuation,
  spaces, blank parts, non-positive revisions, bool revisions, unknown
  descriptor fields, missing descriptor fields, duplicate identities.
- Obsolete structural assumptions not migrated: `PackageServerProduct` closed
  enum, package-owned `ProductCatalog`, and root-package server catalogue
  imports.
- Future owner: core.

### Implementation

The first EXTRACT.C value landed in extracted core as a small stdlib-only module:

```python
@dataclass(frozen=True, order=True)
class ProductIdentity:
    namespace: str
    name: str
    contract_revision: int

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}/{self.contract_revision}"

    def descriptor(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "name": self.name,
            "contract_revision": self.contract_revision,
        }
```

The adjacent codec is deliberately strict. It admits exactly the current
descriptor keys and fails closed for unknown or missing fields:

```python
class ProductIdentityCodec:
    def decode(self, descriptor: Mapping[str, object]) -> ProductIdentity:
        mapping = _mapping(descriptor, "product_identity")
        keys = frozenset(mapping)
        if keys != _DESCRIPTOR_KEYS:
            raise ProductIdentityError(...)
        return ProductIdentity(...)
```

Duplicate detection exists before the future catalogue appears:

```python
def require_unique_product_identities(
    identities: Iterable[ProductIdentity],
) -> tuple[ProductIdentity, ...]:
    values = tuple(identities)
    for identity in values:
        if not isinstance(identity, ProductIdentity):
            raise ProductIdentityError(...)
    ordered = tuple(sorted(values))
    seen: set[ProductIdentity] = set()
    for identity in ordered:
        if identity in seen:
            raise DuplicateProductIdentity(...)
        seen.add(identity)
    return ordered
```

### Decisions

- Unknown namespaces are valid data. `pottery-factory/api/1` is just as much a
  product identity as `cpk-servers/coredns/1`.
- Case is not normalized. Uppercase fails closed so two agents cannot disagree
  about whether `Hello` and `hello` are the same product.
- `contract_revision` is a positive integer, not a semantic-version promise.
  Compatibility policy belongs to later product/catologue admission work.
- The root `control_plane_kit_core` package re-exports the identity values
  because they are pure stdlib-only values. This keeps base import light while
  making the first external-product primitive easy to reach.

### Evidence

- Red evidence: focused core test collection failed only because
  `control_plane_kit_core.products` did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 34 unittest tests,
  compileall, and base import verification.

### Handoff To #621

#621 can build `OciImageReference` beside this identity language. It should use
the same pattern: pure stdlib value, strict adjacent codec, deterministic
descriptor, no registry calls, no Docker imports, and no compatibility-version
machinery. Product identity is now available as
`control_plane_kit_core.products.ProductIdentity`.

## #621 OCI Image Reference

### Law Card

- Reference identity: `EXTRACT.C.2.oci-image-reference`
- Evidence source: rollout issue #621, #620 handoff, and rollout OCI image
  sections.
- Observable law: an external container product may carry a human tag, but
  execution identity is always the immutable digest-pinned reference.
- Object:

```text
OciImageReference
  = registry
  x repository
  x digest
  x optional tag
  x platform constraints
  x provenance fields
```

- Expected result: valid digest-pinned image references construct, encode,
  decode, sort platform constraints, expose a tag-free `execution_reference`,
  and fail platform mismatch before any runtime effect.
- Negative cases: missing digest, mutable tag without digest, malformed digest,
  unsupported digest algorithm, credential-bearing registry, URL registry,
  uppercase/path-abuse repository, malformed tag, secret-like provenance keys,
  unknown/missing descriptor fields, malformed platform descriptors, and invalid
  platform values.
- Obsolete structural assumptions not migrated: command-string images,
  package-owned Docker helpers, live Docker inspection, registry pulls, and
  mutable tags as execution truth.
- Future owner: core.

### Implementation

`OciImageReference` now lives beside `ProductIdentity` as another pure product
boundary value:

```python
@dataclass(frozen=True)
class OciImageReference:
    registry: str
    repository: str
    digest: str
    tag: str | None = None
    platforms: tuple[OciPlatform, ...] = ()
    provenance: Mapping[str, str] | None = None

    @property
    def execution_reference(self) -> str:
        return f"{self.registry}/{self.repository}@{self.digest}"
```

The display reference may carry the mutable tag, but it remains display-only:

```python
@property
def human_reference(self) -> str:
    if self.tag is None:
        return self.execution_reference
    return f"{self.registry}/{self.repository}:{self.tag}@{self.digest}"
```

Platform constraints are pure data and checked before an interpreter can begin:

```python
def require_platform(self, requested: OciPlatform) -> None:
    if self.platforms and requested not in self.platforms:
        raise PlatformMismatch(...)
```

### Decisions

- Only `sha256:<64 lowercase hex>` is admitted right now. That is conservative
  and keeps digest identity unambiguous.
- Tags are optional human hints. They are never used to compute execution truth.
- Registry and repository syntax is bounded and lowercase; registry credentials
  are rejected without echoing the secret-like value in the error message.
- Provenance is bounded string metadata and rejects secret-like keys such as
  `token`, `password`, `secret`, `credential`, and `key`.
- No registry call, Docker import, filesystem access, or network effect appears
  in construction or decoding.

### Evidence

- Red evidence: focused core test collection failed only because the OCI image
  reference names did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 43 unittest tests,
  compileall, and base import verification.

### Handoff To #622

#622 can now compose the product descriptor out of the already extracted pure
languages:

```text
ProductIdentity
  x OciImageReference
  x Protocol / BlockSockets
  x EnvironmentContract
  x RuntimeVariableContract
  x ConfigurationArtifact
  x SecretReference
  x Capability / Verification / Lifecycle values
```

Do not collapse these pieces into one string blob. The product descriptor should
reuse the strict current codecs for each sublanguage, fail closed on unknown
descriptor keys, and continue to avoid Docker, registry, HTTP, MCP, Postgres, or
server-entrypoint imports.

## #622 Runtime Contract Composition

### Law Card

- Reference identity: `EXTRACT.C.3.product-runtime-contract`
- Evidence source: rollout issue #622, #621 handoff, and extracted core contract
  modules.
- Observable law: product runtime material is a typed product of existing closed
  sublanguages, not a free-form metadata dictionary.
- Object:

```text
ProductRuntimeContract
  = BlockSockets
  x PublicStaticEnvironmentBinding*
  x ConfigurationArtifact*
  x SecretDelivery*
  x CapabilityName*
  x VerificationContract
  x ResourceLifecycle
```

- Expected result: a product can describe its sockets, non-secret environment,
  immutable configuration artifacts, secret references, advertised capabilities,
  verification checks, and lifecycle data through one strict descriptor that
  round-trips deterministically.
- Negative cases: verification checks for missing or incompatible provider
  sockets, secret-shaped public environment, secret-shaped configuration
  content, unknown descriptor fields, non-string socket names, secret literals
  in descriptor input, and retained data resource identities overlapping
  configuration artifact identities.
- Obsolete structural assumptions not migrated: capability inference from
  metadata, product-specific environment classes, raw string protocols, inline
  secrets, runtime-derived environment values in product descriptors, and data
  resources represented as configuration files.
- Future owner: core.

### Implementation

The new value composes the already extracted languages directly:

```python
@dataclass(frozen=True)
class ProductRuntimeContract:
    sockets: BlockSockets = field(default_factory=BlockSockets)
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()
    capabilities: tuple[CapabilityName, ...] = ()
    verification: VerificationContract = field(default_factory=VerificationContract)
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)
```

Verification is validated against the product's provider sockets before any
runtime effect can occur:

```python
def _validate_verification(
    sockets: BlockSockets,
    verification: VerificationContract,
) -> None:
    providers = {provider.name: provider for provider in sockets.providers}
    for check in verification.checks:
        provider = providers[check.provider_socket]
        if provider.protocol not in expected_protocols(check):
            raise ProductRuntimeContractError(...)
```

Lifecycle data and configuration artifacts are kept separate:

```python
def _validate_lifecycle_distinctions(lifecycle, configuration_artifacts) -> None:
    retained_data_ids = {value.resource_id for value in lifecycle.data}
    artifact_ids = {value.artifact_id for value in configuration_artifacts}
    if retained_data_ids & artifact_ids:
        raise ProductRuntimeContractError(...)
```

### Decisions

- `ProductRuntimeContract` is not yet `ContainerServerProduct`; it is the shared
  descriptor-safe runtime contract component that #623 will compose with
  `ProductIdentity` and `OciImageReference`.
- Public environment only admits `PublicStaticEnvironmentBinding`. Socket-derived
  values belong to graph realization, not product descriptor authorship.
- Secrets are represented only by `SecretDelivery` values, which contain opaque
  `SecretReference` ids. Resolved secret values remain outside durable graph
  data.
- Capabilities are explicit `CapabilityName` values. The codec does not infer
  them from metadata, routes, labels, or free-form declarations.
- Retained data resources remain lifecycle/data objects and may not be smuggled
  in as configuration artifact identities.

### Evidence

- Red evidence: focused core test collection failed only because
  `ProductRuntimeContract` did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 51 unittest tests,
  compileall, and base import verification.

### Handoff To #623

#623 can now define `ContainerServerProduct` as the direct product:

```text
ContainerServerProduct
  = ProductIdentity
  x OciImageReference
  x ProductRuntimeContract
```

The issue may add a display/server-spec layer if repository evidence shows it is
needed, but it should not duplicate sockets, verification, lifecycle, secret,
configuration, or capability models. Reuse `ProductRuntimeContract` and its
strict codec.

## #623 Container Server Product

### Law Card

- Reference identity: `EXTRACT.C.4.container-server-product`
- Evidence source: rollout issue #623 and #622 handoff.
- Observable law: the first external product form is a pure immutable value that
  composes identity, OCI supply-chain identity, and runtime contract material.
- Object:

```text
ContainerServerProduct
  = ProductIdentity
  x OciImageReference
  x ProductRuntimeContract
  x optional bounded display text
```

- Expected result: products construct, hash, encode, decode, round-trip, and
  expose no callback/import/class-path/shell/host-path field.
- Negative cases: unsupported descriptor variant, unknown descriptor fields,
  host-path-like display metadata, shell-like display metadata, non-container
  product forms, and mutable instance assignment.
- Obsolete structural assumptions not migrated: product Python class paths,
  command strings, Dockerfiles, entrypoints, host bind paths, callbacks, and
  built-in package-owned product names.
- Future owner: core.

### Implementation

The concrete product value is intentionally smaller than the conceptual issue
text because #622 already gathered sockets, configuration, capabilities,
verification, and lifecycle into `ProductRuntimeContract`:

```python
@dataclass(frozen=True)
class ContainerServerProduct:
    identity: ProductIdentity
    image: OciImageReference
    runtime_contract: ProductRuntimeContract
    display_name: str | None = None
    description: str | None = None
```

The descriptor carries an explicit closed variant:

```python
def descriptor(self) -> dict[str, object]:
    return {
        "kind": "container-server",
        "identity": ProductIdentityCodec().encode(self.identity),
        "image": OciImageReferenceCodec().encode(self.image),
        "runtime_contract": ProductRuntimeContractCodec().encode(self.runtime_contract),
        "display_name": self.display_name,
        "description": self.description,
    }
```

### Decisions

- `ContainerServerProduct` does not name Hello, CoreDNS, cpk-server, Docker,
  FastAPI, MCP, or any package-owned server.
- The only admitted product variant is `container-server`. Future product forms
  fail explicitly as unsupported variants.
- Bounded display text is allowed, but it cannot contain shell-like syntax or
  host-path escape hatches.
- OCI provenance now stores internally as a sorted tuple of `(key, value)` pairs
  so `OciImageReference` and therefore `ContainerServerProduct` are hashable
  immutable values.

### Evidence

- Red evidence: focused core test collection failed only because
  `ContainerServerProduct` did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 57 unittest tests,
  compileall, and base import verification.

### Handoff To #624

#624 should turn this strict value codec into the public `product.cpk.json`
descriptor boundary. It should reuse `ContainerServerProductCodec` rather than
adding another product model, and it should add descriptor-file concerns such as
top-level schema shape, byte bounds, canonical JSON ordering, and closed variant
admission.
