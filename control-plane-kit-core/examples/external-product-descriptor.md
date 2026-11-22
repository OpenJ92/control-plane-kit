# External Product Descriptor

`control-plane-kit-core` consumes external products as descriptor data. It does
not import the product package, pull the image, or inspect a Docker daemon.

```python
from pathlib import Path

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import (
    ProductCatalog,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_catalog_product,
)
from control_plane_kit_core.topology import compile_topology

document = ProductDescriptorCodec().decode_document(
    Path("product.cpk.json").read_bytes()
)
catalog = ProductCatalog.empty().add(document)

block = instantiate_catalog_product(
    catalog,
    ProductIdentity("fixture-products", "proxy", 1),
    role_id="proxy-blue",
    configuration=ProductInstanceConfiguration.from_contract(
        document.product.runtime_contract
    ),
)

graph = compile_topology(
    DeploymentTopology("fixture", DockerRuntime(children=(block,)))
)
```

The resulting node is ordinary graph data. Runtime interpreters decide later how
to pull and run the pinned OCI image.

## Required configuration, two concrete instances

A declaration can name a file slot without inventing a default payload. Both
instances below retain the same product identity, registered document digest and
image; their actual file contents differ.

```python
from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration import (
    ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationMediaType,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity,
    ProductInstanceConfiguration, ProductRuntimeContract, instantiate_product,
)
from control_plane_kit_core.topology import compile_topology

slot = ConfigurationArtifactRequirement(
    "settings", "/etc/example/settings.json", ConfigurationMediaType.JSON,
)
product = ContainerServerProduct(
    identity=ProductIdentity("example", "service", 1),
    image=OciImageReference("registry.example", "service", "sha256:" + "a" * 64),
    runtime_contract=ProductRuntimeContract(configuration_requirements=(slot,)),
)
document = ProductDescriptorCodec().encode_document(product)  # product/v2
blocks = tuple(
    instantiate_product(
        product, role,
        ProductInstanceConfiguration(configuration_artifacts=(
            ConfigurationArtifact(slot.artifact_id, slot.target_path, slot.media_type, payload),
        )),
    )
    for role, payload in (("service-a", '{"workers":1}'), ("service-b", '{"workers":2}'))
)
graph = compile_topology(DeploymentTopology("example", DockerRuntime(children=blocks)))
assert all(graph.node(role).metadata["product_descriptor_digest"] == document.content_digest
           for role in ("service-a", "service-b"))
assert graph.node("service-a").configuration_artifacts[0].content_digest != (
    graph.node("service-b").configuration_artifacts[0].content_digest
)
```

The image coordinate is illustrative; this pure example does not pull or run it.
The owning Core tests exercise this composition and exact slot refusals. A slot
does not authorize a receiver or supply trust material. Actual receiver payloads
still pass the strict V2 codec. Operations must match registered slots before
clearing requirements in execution material, preserving the original reference.
