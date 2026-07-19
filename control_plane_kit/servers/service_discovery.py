"""Package-owned service-discovery block contract."""

from __future__ import annotations

from control_plane_kit.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.implementations import PlanOnlyImplementation
from control_plane_kit.types import Protocol


def service_discovery_block(
    block_id: str = "service-discovery",
    *,
    display_name: str = "Service Discovery Registry",
) -> ApplicationBlock:
    """Return the contract-only block; #503 adds its Docker interpreter."""

    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.SERVICE_DISCOVERY,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
        ),
        PlanOnlyImplementation("service-discovery-contract"),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "database",
                    Protocol.POSTGRES,
                    ("DISCOVERY_DATABASE_URL",),
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
