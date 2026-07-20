"""Pure graph-visible declaration and catalog values for server products."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from control_plane_kit.core.algebra import (
    DeployBlock,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.core.control_routes import ControlRouteSetName


class CapabilityImplementation(StrEnum):
    """Closed implementation boundaries that can realize a capability."""

    APPLICATION_PROBE = "application-probe"
    CONTROL_ROUTE = "control-route"
    RUNTIME_LIFECYCLE = "runtime-lifecycle"


@dataclass(frozen=True)
class ExecutableCapability:
    """Evidence that one product can execute an advertised capability."""

    capability: CapabilityName
    implementation: CapabilityImplementation
    route_set: ControlRouteSetName | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        match self.implementation:
            case CapabilityImplementation.APPLICATION_PROBE:
                if self.route_set is not None:
                    raise ValueError("application probe cannot name a control route set")
                if self.path is None or not self.path.startswith("/"):
                    raise ValueError("application probe requires an absolute path")
            case CapabilityImplementation.CONTROL_ROUTE:
                if self.route_set is None:
                    raise ValueError("control route requires a route set")
                if self.path is not None:
                    raise ValueError("control route cannot name an application path")
            case CapabilityImplementation.RUNTIME_LIFECYCLE:
                if self.route_set is not None or self.path is not None:
                    raise ValueError(
                        "runtime lifecycle capability cannot name an application route"
                    )


@dataclass(frozen=True)
class UnsupportedCapability:
    """Explicit closed result for a capability a product does not implement."""

    capability: CapabilityName
    reason: str


CapabilityResolution = ExecutableCapability | UnsupportedCapability


@dataclass(frozen=True)
class ProductDeclaration:
    """One graph-visible server product paired with executable capability truth."""

    product: PackageServerProduct
    maturity: ProductMaturity
    block: DeployBlock
    capabilities: tuple[ExecutableCapability, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.block.spec, PackageServerSpec):
            raise TypeError("product declaration requires PackageServerSpec")
        if self.block.spec.product is not self.product:
            raise ValueError("product declaration product does not match block spec")
        if self.block.spec.maturity is not self.maturity:
            raise ValueError("product declaration maturity does not match block spec")
        implemented = tuple(value.capability for value in self.capabilities)
        if len(set(implemented)) != len(implemented):
            raise ValueError("product declaration repeats a capability")
        if self.block.spec.capabilities != implemented:
            raise ValueError(
                "advertised capabilities must exactly match executable capability evidence"
            )

    def resolve(self, capability: CapabilityName) -> CapabilityResolution:
        """Return executable evidence or an explicit unsupported result."""

        for support in self.capabilities:
            if support.capability is capability:
                return support
        return UnsupportedCapability(
            capability,
            f"{self.product.value} does not implement {capability.value}",
        )

    def descriptor(self) -> dict[str, object]:
        """Return the capability matrix row used by reviews and tooling."""

        return {
            "product": self.product.value,
            "maturity": self.maturity.value,
            "requirements": [
                {
                    "name": socket.name,
                    "protocol": socket.protocol.descriptor(),
                    "binding": socket.binding.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in self.block.sockets.requirements
            ],
            "providers": [
                {"name": socket.name, "protocol": socket.protocol.descriptor()}
                for socket in self.block.sockets.providers
            ],
            "capabilities": [
                {
                    "name": support.capability.value,
                    "implementation": support.implementation.value,
                    **(
                        {"route_set": support.route_set.value}
                        if support.route_set is not None
                        else {}
                    ),
                    **({"path": support.path} if support.path is not None else {}),
                }
                for support in self.capabilities
            ],
        }


@dataclass(frozen=True)
class ProductCatalog:
    """An immutable, identity-unique collection of product declarations."""

    declarations: tuple[ProductDeclaration, ...]
    _by_product: dict[PackageServerProduct, ProductDeclaration] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.declarations, tuple) or not all(
            isinstance(value, ProductDeclaration) for value in self.declarations
        ):
            raise TypeError("product catalog requires product declarations")
        by_product = {value.product: value for value in self.declarations}
        if len(by_product) != len(self.declarations):
            raise ValueError("product catalog repeats a product identity")
        object.__setattr__(self, "_by_product", by_product)

    def declaration(self, product: PackageServerProduct) -> ProductDeclaration:
        """Return the exact declaration for one closed product identity."""

        if not isinstance(product, PackageServerProduct):
            raise TypeError("product catalog lookup requires PackageServerProduct")
        return self._by_product[product]

    def descriptor(self) -> list[dict[str, object]]:
        """Project declarations in stable product-identity order."""

        return [
            value.descriptor()
            for value in sorted(self.declarations, key=lambda item: item.product.value)
        ]
