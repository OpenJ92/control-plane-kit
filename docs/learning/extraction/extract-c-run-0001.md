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
