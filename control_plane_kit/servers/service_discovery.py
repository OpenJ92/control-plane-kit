"""Package-owned service-discovery block contract."""

from control_plane_kit.core.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.core.types import Protocol


def service_discovery_block(
    block_id: str = "service-discovery",
    *,
    display_name: str = "Service Discovery Registry",
    image: str = "control-plane-kit:local",
    identity_secret_reference: str = "secret://service-discovery/identity-attestation",
) -> ApplicationBlock:
    """Return the package-owned Docker service-discovery block."""

    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.SERVICE_DISCOVERY,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
            health_path="/health/ready",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.DISCOVERY_READABLE,
                CapabilityName.DISCOVERY_MUTABLE,
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=("python", "-m", "control_plane_kit.discovery_server.main"),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_DISCOVERY_IDENTITY_TOKEN",
                    SecretReference(identity_secret_reference),
                ),
            ),
        ),
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
