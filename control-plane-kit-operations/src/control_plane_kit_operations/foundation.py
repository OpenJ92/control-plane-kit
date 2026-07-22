"""Inspectable package-boundary facts for the operations distribution."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_core import DeploymentProgramStage


@dataclass(frozen=True)
class OperationsPackageBoundary:
    """Declare the operations package role before service code arrives."""

    distribution: str
    import_package: str
    depends_on: tuple[str, ...]
    deployment_spine: tuple[DeploymentProgramStage, ...]
    future_owners: tuple[str, ...]
    excluded_owners: tuple[str, ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "import_package": self.import_package,
            "depends_on": list(self.depends_on),
            "deployment_spine": [stage.value for stage in self.deployment_spine],
            "future_owners": list(self.future_owners),
            "excluded_owners": list(self.excluded_owners),
        }


OPERATIONS_PACKAGE_BOUNDARY = OperationsPackageBoundary(
    distribution="control-plane-kit-operations",
    import_package="control_plane_kit_operations",
    depends_on=("control-plane-kit-core",),
    deployment_spine=tuple(DeploymentProgramStage),
    future_owners=(
        "DeploymentProgram",
        "Deploy",
        "Postgres schema",
        "PostgresUnitOfWork",
        "store bundle",
        "command services",
        "read projections",
        "RegisteredProduct",
    ),
    excluded_owners=(
        "core pure language",
        "cpk-server process",
        "HTTP framework adapters",
        "MCP process adapter",
        "Docker runtime interpreter",
        "package-owned server products",
    ),
)
