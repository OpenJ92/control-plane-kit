"""Operations command service for desired graph authoring."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Callable

from control_plane_kit_core.delegation_authority import (
    carry_forward_compatible_delegation_verifiers,
    DelegationAuthorityError,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.products import (
    ProductRegistrationError,
    ProductRegistrationNotFound,
    RegisteredProductStatus,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)


class GraphAuthoringError(ValueError):
    """Raised when desired graph authoring violates operations policy."""


@dataclass(frozen=True)
class SetDesiredGraphCommand:
    """Application command to publish desired graph truth for one workspace."""

    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None
    expected_desired_realized_projection_id: str | None = None
    expected_desired_graph_revision: int = 0

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.graph, DeploymentGraph):
            raise GraphAuthoringError("set desired graph requires DeploymentGraph")
        if self.expected_desired_graph_id is not None:
            _validate_text(self.expected_desired_graph_id, "expected_desired_graph_id")
        if self.expected_desired_realized_projection_id is not None:
            _validate_text(
                self.expected_desired_realized_projection_id,
                "expected_desired_realized_projection_id",
            )
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise GraphAuthoringError(
                "expected_desired_graph_revision must be nonnegative"
            )


@dataclass(frozen=True)
class SetDesiredGraphResult:
    """Committed desired graph evidence."""

    workspace: WorkspaceRecord
    graph_version: GraphVersionRecord
    realized_projection: RealizedGraphProjectionRecord
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
    if (
        workspace.desired_graph_id != command.expected_desired_graph_id
        or workspace.desired_realized_projection_id
        != command.expected_desired_realized_projection_id
        or workspace.desired_graph_revision
        != command.expected_desired_graph_revision
    ):
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
    previous_realized_graph = _desired_realized_graph(
        unit_of_work.stores,
        workspace,
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
    realized_projection = unit_of_work.stores.realized_graphs.save(
        _realized_projection_for_authored_graph(
            unit_of_work.stores,
            graph_version,
            command.graph,
            previous_realized_graph,
        )
    )
    updated = unit_of_work.stores.workspaces.set_desired_graph(
        command.workspace_id,
        graph_version.graph_id,
        realized_projection.projection_id,
    )
    return SetDesiredGraphResult(
        workspace=updated,
        graph_version=graph_version,
        realized_projection=realized_projection,
        product_references=product_references,
    )


def _realized_projection_for_authored_graph(
    stores: Any,
    authored_record: GraphVersionRecord,
    authored_graph: DeploymentGraph,
    previous_realized_graph: DeploymentGraph | None = None,
) -> RealizedGraphProjectionRecord:
    bindings = authored_graph.delegation_authorities
    if not bindings:
        return stores.realized_graphs.identity_for_authored(
            authored_record.workspace_id,
            authored_record.graph_id,
        )

    try:
        carried = (
            ()
            if previous_realized_graph is None
            else carry_forward_compatible_delegation_verifiers(
                authored_graph,
                previous_realized_graph,
            )
        )
    except DelegationAuthorityError as error:
        raise GraphAuthoringError(str(error)) from error
    carried_by_identity = {
        projection.binding_identity: projection for projection in carried
    }
    projections: list[DelegationVerifierProjection] = []
    key_scopes: dict[
        tuple[str, str],
        tuple[RegisteredDelegationSigningKey, ...],
    ] = {}
    for binding in bindings:
        scope = (binding.purpose.value, binding.issuer)
        keys = key_scopes.get(scope)
        if keys is None:
            keys = stores.delegation_signing_keys.list_for_projection(
                authored_record.workspace_id,
                binding.purpose,
                binding.issuer,
            )
            key_scopes[scope] = keys
        active = tuple(
            key
            for key in keys
            if key.status is RegisteredDelegationSigningKeyStatus.ACTIVE
        )
        if len(keys) != 1 or len(active) != 1:
            raise GraphAuthoringError(
                "exactly one settled active delegation key is required "
                "for each authored binding"
            )
        public_key = active[0].public_key
        audience = _delegation_audience(
            authored_record.workspace_id,
            binding.delegate_node_id,
            binding.purpose,
        )
        carried_projection = carried_by_identity.get(binding.identity)
        if carried_projection is not None:
            if (
                carried_projection.audience != audience
                or carried_projection.public_keys != (public_key,)
            ):
                raise GraphAuthoringError(
                    "carried delegation verifier projection does not match "
                    "settled delegation key truth"
                )
            projections.append(carried_projection)
            continue
        projection_descriptor = {
            "workspace_id": authored_record.workspace_id,
            "delegate_node_id": binding.delegate_node_id,
            "purpose": binding.purpose.value,
            "issuer": binding.issuer,
            "public_key": public_key.descriptor(),
        }
        projection_digest = sha256(
            json.dumps(
                projection_descriptor,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        projections.append(
            DelegationVerifierProjection(
                delegate_node_id=binding.delegate_node_id,
                purpose=binding.purpose,
                issuer=binding.issuer,
                audience=audience,
                projection_id=f"delegation-{projection_digest}",
                public_keys=(public_key,),
            )
        )
    try:
        realized_graph = materialize_delegation_verifiers(
            authored_graph,
            tuple(projections),
        )
    except DelegationAuthorityError as error:
        raise GraphAuthoringError(str(error)) from error
    draft = RealizedGraphProjectionRecord.from_graph(
        projection_id="projection-pending",
        workspace_id=authored_record.workspace_id,
        source_authored_graph_id=authored_record.graph_id,
        projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
        projection_key="initial-delegation-verifier",
        graph=realized_graph,
        created_by=authored_record.created_by,
        created_at=authored_record.created_at,
    )
    return replace(draft, projection_id=f"projection-{draft.projection_digest}")


def _desired_realized_graph(
    stores: Any,
    workspace: WorkspaceRecord,
) -> DeploymentGraph | None:
    if workspace.desired_graph_id is None:
        if workspace.desired_realized_projection_id is not None:
            raise GraphAuthoringError(
                "workspace desired realized pointer has no authored graph"
            )
        return None
    if workspace.desired_realized_projection_id is None:
        raise GraphAuthoringError(
            "workspace desired authored graph has no realized projection"
        )
    try:
        realized_record = stores.realized_graphs.get(
            workspace.desired_realized_projection_id
        )
    except KeyError as error:
        raise GraphAuthoringError(
            "workspace desired realized pointer has no projection truth"
        ) from error
    if (
        realized_record.workspace_id != workspace.workspace_id
        or realized_record.source_authored_graph_id != workspace.desired_graph_id
    ):
        raise GraphAuthoringError(
            "workspace desired realized projection does not match authored truth"
        )
    try:
        authored_record = stores.graphs.get(workspace.desired_graph_id)
    except KeyError as error:
        raise GraphAuthoringError(
            "workspace desired authored pointer has no graph truth"
        ) from error
    if authored_record.workspace_id != workspace.workspace_id:
        raise GraphAuthoringError(
            "workspace desired authored graph belongs to another workspace"
        )
    try:
        authored_graph = DEFAULT_GRAPH_CODEC.decode(authored_record.graph_descriptor)
        realized_graph = DEFAULT_GRAPH_CODEC.decode(realized_record.graph_descriptor)
    except ValueError as error:
        raise GraphAuthoringError(
            "workspace desired realized projection is malformed"
        ) from error
    projected_authored_graph = realized_graph
    for node_id in sorted(realized_graph.nodes):
        node = projected_authored_graph.node(node_id)
        if node.delegation_verifier_projection is not None:
            projected_authored_graph = projected_authored_graph.update_node(
                replace(node, delegation_verifier_projection=None)
            )
    if projected_authored_graph != authored_graph:
        raise GraphAuthoringError(
            "workspace desired realized projection does not match authored graph truth"
        )
    return realized_graph


def _delegation_audience(
    workspace_id: str,
    delegate_node_id: str,
    purpose: DelegationKeyPurpose,
) -> str:
    if purpose is DelegationKeyPurpose.GATEWAY_PROBE:
        return f"gateway:{workspace_id}:{delegate_node_id}"
    raise GraphAuthoringError("delegation key purpose cannot be materialized")


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
