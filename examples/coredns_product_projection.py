"""Pure discovery-to-CoreDNS product projection.

The example makes the product boundary visible:

    discovery records
      -> CoreDnsConfiguration
        -> ConfigurationArtifact values
          -> CoreDNS ApplicationBlock
            -> DeploymentGraph
              -> GraphDiff

A and AAAA records intentionally preserve addresses, not endpoint ports. A
future SRV product language can represent port-bearing DNS service discovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from control_plane_kit import (
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    Endpoint,
    EndpointScope,
    LiteralAddress,
    Protocol,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from control_plane_kit.domains.discovery import (
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
)
from control_plane_kit.products.servers import (
    DnsName,
    coredns_block,
    project_discovery_to_coredns,
    render_coredns_configuration,
)


def coredns_product_projection():
    observed_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    registration = DiscoveryRegistrationRecord(
        DiscoveryRegistration(
            DiscoveryIdentity("workspace-a", "orders", "blue"),
            Endpoint(
                LiteralAddress("http://10.0.0.42:8080"),
                Protocol.HTTP,
                EndpointScope.PRIVATE,
            ),
            DiscoveryRegistrationMode.CONTROL_PLANE,
            DiscoveryLease(observed_at, observed_at + timedelta(minutes=5)),
        ),
        DiscoveryRegistrationStatus.ACTIVE,
        1,
        observed_at,
    )
    configuration = project_discovery_to_coredns(
        DnsName("cpk.internal"),
        (registration,),
        observed_at=observed_at,
    )
    artifacts = render_coredns_configuration(configuration)
    block = coredns_block(configuration=configuration)
    desired = compile_recipe(
        DeploymentRecipe(
            "coredns-projection",
            DockerRuntime(runtime_id="dns", children=(block,)),
        )
    )
    change = diff_graphs(
        validate_graph(DeploymentGraph(desired.name)),
        validate_graph(desired),
    )
    return registration, configuration, artifacts, block, desired, change
