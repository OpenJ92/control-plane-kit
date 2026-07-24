"""Operations command service for desired graph authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
)
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.products import (
    ProductRegistrationError,
    ProductRegistrationNotFound,
    RegisteredProductStatus,
)
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord


class GraphAuthoringError(ValueError):
    """Raised when desired graph authoring violates operations policy."""


@dataclass(frozen=True)
class SetDesiredGraphCommand:
    """Application command to publish desired graph truth for one workspace."""

    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.graph, DeploymentGraph):
            raise GraphAuthoringError("set desired graph requires DeploymentGraph")
        if self.expected_desired_graph_id is not None:
            _validate_text(self.expected_desired_graph_id, "expected_desired_graph_id")


@dataclass(frozen=True)
class SetDesiredGraphResult:
    """Committed desired graph evidence."""

    workspace: WorkspaceRecord
    graph_version: GraphVersionRecord
    product_references: tuple[ProductReference, ...]


@dataclass(frozen=True)
class SelectableProduct:
    """Secret-free product option admitted for graph authoring."""

    reference: ProductReference
    display_name: str
    description: str | None


class GraphAuthoringService:
    """Application service that owns desired-graph transaction boundaries."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        graph_id_factory: Callable[[], str],
        clock: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._graph_id_factory = graph_id_factory
        self._clock = clock

    def set_desired_graph(
        self,
        command: SetDesiredGraphCommand,
    ) -> SetDesiredGraphResult:
        if not isinstance(command, SetDesiredGraphCommand):
            raise GraphAuthoringError("set_desired_graph requires SetDesiredGraphCommand")
        with self._unit_of_work_factory() as unit_of_work:
            result = set_desired_graph_in_unit_of_work(
                unit_of_work,
                command,
                graph_id=self._graph_id_factory(),
                created_at=self._clock(),
            )
            unit_of_work.commit()
            return result

    def selectable_products(self, workspace_id: str) -> tuple[SelectableProduct, ...]:
        _validate_text(workspace_id, "workspace_id")
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.registered_products.list_active(workspace_id)
            return tuple(
                SelectableProduct(
                    reference=value.reference,
                    display_name=value.descriptor_document.product.display_name,
                    description=value.descriptor_document.product.description,
                )
                for value in registered
            )


def set_desired_graph_in_unit_of_work(
    unit_of_work: Any,
    command: SetDesiredGraphCommand,
    *,
    graph_id: str,
    created_at: str,
) -> SetDesiredGraphResult:
    """Persist desired graph truth on the caller's transaction boundary."""

    if not isinstance(command, SetDesiredGraphCommand):
        raise GraphAuthoringError("set_desired_graph requires SetDesiredGraphCommand")
    _validate_text(graph_id, "graph_id")
    _validate_text(created_at, "created_at")
    product_references = product_references_in_graph(command.graph)
    workspace = unit_of_work.stores.workspaces.get_for_update(
        command.workspace_id,
    )
    if workspace.desired_graph_id != command.expected_desired_graph_id:
        raise GraphAuthoringError("stale desired graph pointer")
    for reference in product_references:
        try:
            registered = unit_of_work.stores.registered_products.get(
                command.workspace_id,
                reference,
            )
        except ProductRegistrationNotFound as error:
            raise GraphAuthoringError(
                f"unregistered product {reference.identity.key}"
            ) from error
        if registered.status is not RegisteredProductStatus.ACTIVE:
            raise GraphAuthoringError(
                f"unregistered product {reference.identity.key}"
            )
    graph_version = GraphVersionRecord.from_graph(
        graph_id=graph_id,
        workspace_id=command.workspace_id,
        version=unit_of_work.stores.graphs.next_version_for_workspace(
            command.workspace_id
        ),
        graph=command.graph,
        created_by=command.actor_id,
        created_at=created_at,
    )
    unit_of_work.stores.graphs.save(graph_version)
    updated = unit_of_work.stores.workspaces.set_desired_graph(
        command.workspace_id,
        graph_version.graph_id,
    )
    return SetDesiredGraphResult(
        workspace=updated,
        graph_version=graph_version,
        product_references=product_references,
    )


def product_references_in_graph(graph: DeploymentGraph) -> tuple[ProductReference, ...]:
    """Extract pinned product references from product-instantiated graph nodes."""

    if not isinstance(graph, DeploymentGraph):
        raise GraphAuthoringError("product references require DeploymentGraph")
    references: set[ProductReference] = set()
    for node in graph.nodes.values():
        identity_value = node.metadata.get("product_identity")
        digest_value = node.metadata.get("product_descriptor_digest")
        if identity_value is None and digest_value is None:
            continue
        if not isinstance(identity_value, str) or not isinstance(digest_value, str):
            raise GraphAuthoringError(
                f"node {node.node_id!r} has malformed product reference metadata"
            )
        references.add(
            ProductReference(
                identity=_product_identity_from_key(identity_value),
                descriptor_sha256=ProductDescriptorDigest(digest_value),
            )
        )
    return tuple(sorted(references))


def _product_identity_from_key(value: str) -> ProductIdentity:
    parts = value.split("/")
    if len(parts) != 3:
        raise GraphAuthoringError("product identity key must have namespace/name/revision")
    try:
        revision = int(parts[2])
    except ValueError as error:
        raise GraphAuthoringError("product identity revision must be an integer") from error
    return ProductIdentity(parts[0], parts[1], revision)


def _validate_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise GraphAuthoringError(f"{field} must be nonempty bounded text")
    if any(ord(character) < 32 for character in value):
        raise GraphAuthoringError(f"{field} must not contain control characters")
