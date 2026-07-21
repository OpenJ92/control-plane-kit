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
