Source: [__init__.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py).
Maintain this companion alongside its source.

The package facade additionally exports the seven receiver-trust contract names
from `health_receiver_trust`: the gateway/workload result families, selection,
decoder protocol, exact binding, frozen registry and bounded error. Concrete
product decoders remain outside Operations. Importing these names constructs
only immutable empty default registries; it performs no provider or database
work. Existing exports are preserved.
