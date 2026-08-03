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

## #624 product.cpk.json Descriptor Boundary

### Law Card

- Reference identity: `EXTRACT.C.5.product-cpk-json-descriptor`
- Evidence source: rollout issue #624, #623 handoff, and the extracted product
  value language.
- Observable law: `product.cpk.json` is a deterministic language-neutral file
  boundary around `ContainerServerProduct`, not another product model and not an
  execution hook.
- Object:

```text
ProductDescriptorDocument
  = product: ContainerServerProduct
  x content: canonical UTF-8 JSON bytes
```

- Transformation:

```text
ContainerServerProduct
  -> ProductDescriptorCodec.encode_document
    -> ProductDescriptorDocument

bytes | text | mapping
  -> ProductDescriptorCodec.decode_document
    -> ProductDescriptorDocument
```

- Expected result: valid products encode to stable compact JSON with an exact
  top-level schema, decode from file bytes/text/mappings, preserve product
  equality, and expose filename, media type, byte size, and SHA-256 digest as
  derived properties.
- Negative cases: malformed JSON, non-canonical file bytes, unknown top-level
  keys, unsupported schema names, unsupported product variants, nested product
  escape hatches such as `class_path`, oversized documents, and non-JSON-shaped
  mapping values.
- Obsolete structural assumptions not migrated: Python product class imports,
  compatibility v1/v2 wrappers, catalogue admission, registry calls, Docker
  inspection, server names as built-ins, and file metadata supplied as trusted
  descriptor fields.
- Future owner: core.

### Implementation

`ProductDescriptorDocument` is deliberately small. It stores the product value
and the canonical bytes that represent it; file metadata is derived rather than
accepted from untrusted input:

```python
@dataclass(frozen=True)
class ProductDescriptorDocument:
    product: ContainerServerProduct
    content: bytes

    @property
    def filename(self) -> str:
        return "product.cpk.json"

    @property
    def media_type(self) -> str:
        return "application/vnd.cpk.product+json"

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()
```

The descriptor codec wraps the existing product codec instead of creating a
parallel model:

```python
def _document_mapping(self, product: ContainerServerProduct) -> dict[str, object]:
    return {
        "schema": "control-plane-kit.product",
        "product": ContainerServerProductCodec().encode(product),
    }
```

File bytes and text must already be canonical. Mappings can be normalized into
canonical bytes because they represent in-process structured data rather than a
claimed persisted file:

```python
if isinstance(document, bytes):
    mapping = self._json_mapping(document)
    canonical = self._canonical_bytes(mapping)
    if document != canonical:
        raise ProductDescriptorError("product descriptor JSON is not canonical")
```

### Decisions

- The top-level schema is exactly `control-plane-kit.product` in the unreleased
  current language. There is no v1/v2 compatibility layer.
- Canonical JSON is compact UTF-8 with deterministic key insertion from the
  current descriptor constructors. No trailing newline is emitted.
- Descriptor digest is SHA-256 of canonical bytes. It is derived by the document
  and is not a trusted descriptor field.
- `ProductDescriptorCodec` has a configurable byte bound for tests and defaults
  to the same 256 KiB order of magnitude as configuration artifacts.
- `product.cpk.json` admission remains pure. No catalogue mutation, filesystem
  access, Docker action, registry pull, importlib lookup, or product package
  import occurs here.

### Evidence

- Red evidence: focused core test collection failed only because
  `ProductDescriptorCodec` did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 63 unittest tests,
  compileall, and base import verification.
- `git diff --check` passed.

### Handoff To #625

#625 can build `ProductCatalog` on top of the descriptor boundary. It should
admit `ProductDescriptorDocument` and/or decoded `ContainerServerProduct`
values, enforce duplicate identity failure with the existing
`require_unique_product_identities()` primitive, and keep catalogue admission
pure. Do not add registry access, Docker pulls, filesystem scans, mutable global
catalogues, package-owned server imports, or built-in cpk-server special cases.

## #625 Immutable ProductCatalog Composition

### Law Card

- Reference identity: `EXTRACT.C.6.product-catalog-composition`
- Evidence source: rollout issue #625, #624 handoff, and the immutable product
  descriptor document boundary.
- Observable law: a product catalogue is an immutable pure composition of
  already-admitted descriptor documents. It is not a filesystem scanner,
  registry client, plugin loader, mutable global table, or built-in product
  enum.
- Object:

```text
ProductCatalog
  = sorted unique ProductDescriptorDocument*
```

- Partial composition:

```text
empty.add(document) -> ProductCatalog
catalog.merge(other) -> ProductCatalog
catalog.lookup(identity) -> ProductDescriptorDocument | UnknownProductIdentity
```

- Expected result: empty catalogue is identity, add is idempotent for identical
  descriptor replay, merge is associative when product identities do not
  conflict, lookup is explicit, descriptor ordering is deterministic, and digest
  is derived from canonical catalogue bytes.
- Negative cases: non-document catalogue entries, unknown lookups, same product
  identity with different descriptor digests, merge conflicts, dynamic imports,
  filesystem loading, hidden Docker/registry effects, and built-in package-owned
  product assumptions.
- Obsolete structural assumptions not migrated: server product enums, catalogue
  globals, package import discovery, registry pulls during admission, and
  descriptor identity derived from mutable OCI tags.
- Future owner: core.

### Implementation

The catalogue is a value over `ProductDescriptorDocument`, not over product
implementation classes:

```python
@dataclass(frozen=True)
class ProductCatalog:
    products: tuple[ProductDescriptorDocument, ...] = ()
```

Construction sorts by structural identity and collapses exact replay. A repeated
identity with different canonical bytes is a conflict:

```python
for document in documents:
    identity = document.product.identity
    existing = by_identity.get(identity)
    if existing is None:
        by_identity[identity] = document
    elif existing.content_digest != document.content_digest:
        raise ProductCatalogConflict(...)
```

Merge is implemented through repeated `add`, so the same conflict and replay
rules apply to all catalogue composition paths:

```python
def merge(self, other: ProductCatalog) -> ProductCatalog:
    catalog = self
    for document in other.products:
        catalog = catalog.add(document)
    return catalog
```

### Decisions

- Exact repeated descriptor admission is not an error; it is replay and returns
  the same catalogue object from `add()` when possible.
- Same identity with different descriptor digest is the meaningful duplicate
  conflict and fails closed.
- Unknown lookup raises `UnknownProductIdentity` instead of returning an
  ambiguous null-shaped value.
- Catalogue content and digest are derived from deterministic descriptor JSON;
  they are not supplied as trusted fields.
- The catalogue still performs no file I/O. Loading directories or fetching
  descriptors belongs outside core.

### Evidence

- Red evidence: focused core test collection failed only because
  `ProductCatalog` did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 70 unittest tests,
  compileall, and base import verification.

### Handoff To #626

#626 can now bind a catalogued product into a deployment topology. It should
accept a `ProductCatalog` plus structural product identity, resolve the product
through `lookup()`, and produce ordinary core topology values. Do not let the
topology compiler import product packages, read `product.cpk.json` files, pull
OCI images, or infer package-owned servers. Unknown product identity must remain
an explicit failure.

## #626 Pure Product Instantiation Into DeployBlock

### Law Card

- Reference identity: `EXTRACT.C.7.product-instantiation`
- Evidence source: rollout issue #626, #625 handoff, and the existing topology
  compiler/graph descriptor pipeline.
- Observable law: an admitted external product can be instantiated into an
  ordinary `ApplicationBlock` without importing product code, reading files,
  resolving secrets, pulling OCI images, creating connections, or performing a
  runtime effect.
- Objects:

```text
ProductInstanceConfiguration
  = public environment bindings
  x configuration artifacts
  x secret deliveries

OciContainerProductImplementation
  = ProductDescriptorDocument
  x ProductInstanceConfiguration

ProductMaterializedBlock
  = endpoints
  x non-secret env
  x config artifacts
  x unresolved secret deliveries
  x lifecycle
  x metadata
```

- Transformations:

```text
ContainerServerProduct x RoleId x ProductInstanceConfiguration
  -> instantiate_product
    -> ApplicationBlock

ProductCatalog x ProductIdentity x RoleId x ProductInstanceConfiguration
  -> instantiate_catalog_product
    -> ApplicationBlock

ApplicationBlock
  -> compile_topology
    -> DeploymentGraph
```

- Expected result: instantiated products compile into ordinary graph nodes,
  preserve socket identity, retain unresolved secret references, preserve
  configuration artifacts, expose descriptor digest/image/product metadata, and
  round-trip through the existing `GraphDescriptorCodec`.
- Negative cases: malformed role ids, missing configuration, extra
  configuration, unknown catalogue lookup, non-product values, non-configuration
  values, filesystem loading, dynamic imports, Docker pulls, registry calls, and
  product-owned socket connections.
- Obsolete structural assumptions not migrated: package-specific block classes,
  class paths, command strings, Dockerfile fields, automatic connection
  construction, and runtime-specific host publication during pure instantiation.
- Future owner: core.

### Implementation

The public configuration object is intentionally just the currently extracted
configuration material. It proves strict matching today without inventing
placeholder/template semantics prematurely:

```python
@dataclass(frozen=True)
class ProductInstanceConfiguration:
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()
```

The catalogue path resolves product truth first and then instantiates the exact
descriptor document:

```python
def instantiate_catalog_product(catalog, identity, *, role_id, configuration):
    return _instantiate_document(catalog.lookup(identity), role_id, configuration)
```

The resulting block is a normal `ApplicationBlock`; the existing topology
compiler is not taught a special product lane:

```python
return ApplicationBlock(
    spec=BlockSpec(...),
    implementation=OciContainerProductImplementation(document, configuration),
    sockets=product.runtime_contract.sockets,
)
```

Materialization remains pure. The implementation creates deterministic private
placeholder endpoints from role/socket/protocol so graph edges can still derive
environment bindings without claiming live reachability:

```python
endpoints={
    socket.name: Endpoint(
        LiteralAddress(_private_endpoint(block_id, socket.name, socket.protocol)),
        socket.protocol,
    )
    for socket in sockets.providers
}
```

### Decisions

- `instantiate_product()` computes a canonical `ProductDescriptorDocument` from
  the product so descriptor digest remains available even without a catalogue.
- `instantiate_catalog_product()` is preferred once catalogue admission exists
  because it uses the exact admitted document from `ProductCatalog.lookup()`.
- Connections remain graph-owned. Instantiation only emits a block with the
  product's provider and requirement sockets.
- Configuration matching is exact by public environment names, configuration
  artifact identity/path/media/file mode, and secret delivery identity. Values
  may differ where the identity permits it, such as public environment values.
- Provider endpoints are deterministic graph placeholders. Runtime observation
  and health remain later interpreter responsibilities.

### Evidence

- Red evidence: focused core test collection failed only because
  `ProductInstanceConfiguration` and instantiation functions did not yet exist.
- Green evidence: `./control-plane-kit-core/test.sh` passed 76 unittest tests,
  compileall, and base import verification.

### Handoff To #627

#627 should prove the instantiated external product participates in the existing
topology/diff/planning path. Use `ProductCatalog`, `instantiate_catalog_product`,
`compile_topology`, `validate_graph`, `diff_graphs`, and `compile_activity_plan`
as composition points. Do not add a parallel product graph, product diff, or
product plan. Unknown products and invalid configuration should remain pure
failures before planning.

## #627 Product Truth Through Graph, Diff, And Plan

### Law Card

- Reference identity: `EXTRACT.C.8.product-truth-propagation`
- Evidence source: rollout issue #627 and the #626 instantiated product graph
  proof.
- Observable law: product revision, descriptor digest, and OCI image digest are
  graph truth once an external product is instantiated. They survive graph
  descriptors, produce explicit graph diffs when changed, and compile into the
  existing activity-plan language without a product-specific planning lane.
- Objects reused:

```text
ProductCatalog
ProductDescriptorDocument
ProductInstanceConfiguration
ApplicationBlock
DeploymentGraph
GraphDiff
ActivityPlan
```

- Transformations proved:

```text
ProductCatalog x ProductIdentity x RoleId x ProductInstanceConfiguration
  -> instantiate_catalog_product
    -> ApplicationBlock
      -> compile_topology
        -> GraphDescriptorCodec.encode/decode
          -> diff_graphs
            -> compile_activity_plan
```

- Expected result: `product_identity`, `product_descriptor_digest`, and
  `oci_image` metadata survive graph descriptor round-trip; product image digest
  changes produce a node metadata diff; product revision changes are not erased
  by reusing the same role id; initial deployment uses ordinary start/wait plan
  operations; product updates use ordinary reconciliation.
- Negative cases covered by upstream tests: unknown catalogue lookup, invalid
  configuration, malformed role id, secret-bearing descriptors, runtime effects
  during instantiation, and product-owned connections.
- Obsolete structural assumptions not migrated: product-specific graph codec,
  product-specific diff, product-specific activity plan, and graph drift
  retargeting admitted work by changing hidden implementation state.
- Future owner: core for graph/diff/plan propagation; operations/interpreters
  for effect material, observations, and read projections.

### Implementation

#627 required no production code. The existing data path already preserved the
new product metadata introduced by #626:

```python
metadata={
    "product_identity": self.document.product.identity.key,
    "product_descriptor_digest": self.document.content_digest,
    "oci_image": self.document.product.image.execution_reference,
}
```

The new test intentionally exercises the ordinary compiler path:

```python
block = instantiate_catalog_product(...)
graph = compile_topology(DeploymentTopology("hello", DockerRuntime(children=(block,))))
restored = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(graph))
```

Product changes then appear as regular structural changes:

```python
diff = diff_graphs(validate_graph(before), validate_graph(after))
plan = compile_activity_plan(diff)
```

### Decisions

- This issue stays inside extracted core. Effect material, observations, and read
  projections are named in the issue text but are not present in
  `control-plane-kit-core` yet. They become downstream obligations for the
  operations/interpreter extraction rather than fake core APIs.
- Product identity/digest are currently represented as node metadata because
  graph nodes already preserve metadata through codec/diff/plan. A future typed
  product block-spec variant may be useful, but was not necessary for the core
  propagation law.
- Changing OCI image digest or product revision is a structural graph change;
  it is not hidden inside runtime implementation state.

### Evidence

- Red evidence: the first test pass failed because the test guessed wrong field
  names in the existing diff algebra (`FieldSubject.owner` and
  `MetadataValue.values`). The test was corrected to use the established
  algebra; no production aliases were added.
- Green evidence: `./control-plane-kit-core/test.sh` passed 80 unittest tests,
  compileall, and base import verification.

### Handoff To #628

#628 should harden security/architecture around the product path now that
descriptor, catalogue, instantiation, and graph/diff/plan propagation exist.
Focus on secret exclusion, no optional dependency imports, no product package
imports, no dynamic loading, no file/network/Docker effects in core, and no
parallel product graph/planning model. If additional AST policies are added,
keep them pointed at structural laws rather than incidental implementation text.

## #628 Malicious Descriptor And Catalogue Hardening

### Law Card

- Reference identity: `EXTRACT.C.9.product-descriptor-hardening`
- Evidence source: rollout issue #628 and the descriptor/catalogue/product
  propagation stack from #624 through #627.
- Observable law: malicious external product descriptors fail before admission,
  effects, or catalogue mutation. Rejection is bounded and does not echo
  secret-shaped canaries.
- Objects reused:

```text
ProductDescriptorCodec
ProductCatalog
ContainerServerProduct
OciImageReference
ProductRuntimeContract
```

- Inputs exercised:

```text
credential-bearing registry
reserved container path
unknown protocol application
shell-like description text
descriptor-claimed registry policy
same identity / different descriptor digest
dynamic loading AST canaries
```

- Expected result: all malicious descriptor cases fail at pure decode or
  construction boundaries; conflict rejection leaves the prior immutable
  catalogue unchanged; product source contains no dynamic import, eval, exec,
  file-open, Docker, FastAPI, httpx, MCP, psycopg, subprocess, or uvicorn import
  dependency.
- Negative cases not applicable inside extracted core: transaction rollback and
  durable admission mutation. There is no UnitOfWork or admission store in
  `control-plane-kit-core` yet, so those laws remain operations-layer work.
- Future owner: core for pure rejection/AST policy; operations for durable
  admission atomicity; interpreters for runtime registry/Docker policy.

### Implementation

#628 required no production code. It added a hardening test matrix around the
current descriptor boundary:

```python
with self.assertRaises(ProductDescriptorError) as caught:
    ProductDescriptorCodec().decode_document(descriptor)
self.assertNotIn(SECRET_CANARY, str(caught.exception))
```

The catalogue mutation proof uses immutable composition directly:

```python
catalog = ProductCatalog.empty().add(first)
with self.assertRaises(ProductCatalogConflict):
    catalog.add(second)
self.assertEqual(catalog.products, (first,))
```

The AST check is deliberately scoped to the product language module rather than
the whole package, because this issue hardens external product admission:

```python
forbidden_calls = {"__import__", "eval", "exec", "open"}
forbidden_import_roots = {"docker", "fastapi", "httpx", "importlib", ...}
```

### Decisions

- Allowed registries are policy, not descriptor claims. A descriptor field such
  as `allowed_registry` is rejected as an unknown product key.
- Secret canaries are asserted against the raised top-level error text. Nested
  exception causes may preserve debugging structure, but user-facing error text
  remains bounded and generic.
- Transaction rollback was not faked in core. Durable catalogue admission will
  need a UnitOfWork law in the operations package.

### Evidence

- Green evidence: `./control-plane-kit-core/test.sh` passed 83 unittest tests,
  compileall, and base import verification.
- No production code changed.

### Handoff To #629

#629 should close the EXTRACT.C foundation. Run the complete core validation,
review module boundaries, confirm the product language remains pure and
dependency-light, summarize all product objects/morphisms/laws, and state the
handoff to the next rollout step. In particular, #629 should call out the known
future work: durable product admission transactions, operations-layer effect
material/read projections, external server repository descriptors, and the
future `cpk-server` product descriptor living outside core.

## #629 External Fixture And EXTRACT.C Closeout

### Law Card

- Reference identity: `EXTRACT.C.10.external-fixture-closeout`
- Evidence source: rollout issue #629 and the completed EXTRACT.C child issues
  #620 through #628.
- Observable law: core can consume an external `product.cpk.json` descriptor
  fixture without importing the product package, instantiate it as an ordinary
  deployment block, and traverse the pure graph/diff/plan pipeline.
- Fixture:

```text
control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json
```

- Pipeline proved:

```text
canonical product.cpk.json bytes
  -> ProductDescriptorCodec.decode_document
    -> ProductCatalog.add
      -> instantiate_catalog_product
        -> ApplicationBlock
          -> compile_topology
            -> GraphDescriptorCodec.encode/decode
              -> diff_graphs
                -> compile_activity_plan
```

- Expected result: descriptor consumption imports no fixture package, product
  identity/digest/OCI image remain graph metadata, and the plan uses ordinary
  start activities.
- Negative cases carried from prior issues: malformed descriptors, non-canonical
  bytes, malicious registries, path abuse, secret canaries, duplicate conflicts,
  unknown protocols, invalid configuration, and dynamic loading canaries.
- Future owner: core for pure descriptor/pipeline law; server-product package
  for actual OCI images; operations/interpreters for durable admission,
  materialized effects, observations, and read projections.

### Product Language Objects

EXTRACT.C established these core objects:

```text
ProductIdentity
OciPlatform
OciImageReference
ProductRuntimeContract
ContainerServerProduct
ProductDescriptorDocument
ProductCatalog
ProductInstanceConfiguration
OciContainerProductImplementation
```

The main morphisms now available are:

```text
ProductIdentityCodec.decode
OciImageReferenceCodec.decode
ProductRuntimeContractCodec.decode
ContainerServerProductCodec.decode
ProductDescriptorCodec.decode_document
ProductCatalog.add / merge / lookup
instantiate_product
instantiate_catalog_product
compile_topology
diff_graphs
compile_activity_plan
```

### Implementation

The fixture test deliberately starts from bytes on disk:

```python
document = ProductDescriptorCodec().decode_document(FIXTURE.read_bytes())
catalog = ProductCatalog.empty().add(document)
block = instantiate_catalog_product(...)
```

The public example mirrors that descriptor-first path in:

```text
control-plane-kit-core/examples/external-product-descriptor.md
```

### Decisions

- The external fixture is a descriptor fixture outside the core import package,
  not a wheel or OCI image. A real external server repository/container belongs
  to the next rollout stage.
- The fixture file must be byte-canonical. The first validation failed because
  the patch-created JSON file had a trailing newline. The fix removed the final
  newline and kept `ProductDescriptorCodec` strict.
- No root import or package boundary changed during closeout.
- `cpk-server` remains future server-product work outside core. Core now has
  the generic language needed to represent it as an ordinary
  `ContainerServerProduct` descriptor later.

### Validation Evidence

- `git diff --check` passed.
- `./control-plane-kit-core/test.sh` passed 84 unittest tests, compileall, and
  base install/import verification.
- Core module inventory remains exact at 22 source modules.
- Successor test inventory now has 16 unittest modules.
- Docker residue audit found no running CPK containers; only Pottery Factory
  containers were running and were left untouched.

### Residual Risks And Handoff

- Durable product admission needs an operations-layer UnitOfWork and rollback
  law. Core does not have durable stores.
- Product descriptor retrieval from URLs, signature verification, registry
  allow/deny policy, OCI image pulls, and runtime material belong outside core.
- Effect material, observations, read projections, HTTP API, MCP API, and
  cpk-server packaging remain future extraction/server-product work.
- The future `control-plane-kit-servers` package should use the public
  `product.cpk.json` language rather than reintroducing package-owned server
  enums or core imports of server implementations.
