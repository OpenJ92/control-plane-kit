"""Operations command service for desired graph authoring."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference
from control_plane_kit_core.topology.secret_selection import carry_forward_secret_selections

from control_plane_kit_core.node_control import (
    NodeControlContractError,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
)
from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, Node
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProviderStatus, SecretProviderRegistrationError, _validate_reference_admission,
)
from control_plane_kit_operations.products import (
    ProductRegistrationError,
    ProductRegistrationNotFound,
    RegisteredProductStatus,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.receiver_lifecycle import (
    ReceiverLifecycleStorageError, _retained_receiver_material, derive_receiver_bindings,
)


class GraphAuthoringError(ValueError):
    """Raised when desired graph authoring violates operations policy."""


class GraphIdentityConflict(GraphAuthoringError):
    """Raised when immutable graph identity cannot be freshly admitted."""


def _selection_error():
    raise GraphAuthoringError("secret selection preparation is invalid or unavailable")


def _metadata_text(value):
    if (type(value) is not str or not 1 <= len(value) <= 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _selection_error()


@dataclass(frozen=True, slots=True)
class SecretVersionMetadataRequest:
    workspace_id: str
    actor_subject: str
    correlation_id: str
    provider_registration_id: str
    reference_registration_id: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: SecretReference
    selection: SecretVersionSelection

    def __post_init__(self):
        for value in (self.workspace_id, self.actor_subject, self.correlation_id,
                self.provider_registration_id, self.reference_registration_id):
            _metadata_text(value)
        if (type(self.endpoint_reference) is not SecretProviderEndpointReference
                or type(self.credential_reference) is not SecretReference
                or type(self.selection) is not SecretVersionSelection):
            _selection_error()
        SecretVersionSelection.__post_init__(self.selection)


@dataclass(frozen=True, slots=True)
class SecretVersionMetadata:
    correlation_id: str
    provider_registration_id: str
    reference_registration_id: str
    selection: SecretVersionSelection
    available: bool

    def __post_init__(self):
        for value in (self.correlation_id, self.provider_registration_id, self.reference_registration_id):
            _metadata_text(value)
        if type(self.selection) is not SecretVersionSelection or type(self.available) is not bool:
            _selection_error()
        SecretVersionSelection.__post_init__(self.selection)


class SecretVersionMetadataReader(Protocol):
    def read_version(self, request: SecretVersionMetadataRequest) -> SecretVersionMetadata: ...


def validate_proposed_graph_id(value: str) -> None:
    """Admit a public graph-revision name without granting graph authority."""

    if type(value) is str:
        try:
            NodeControlGraphReference(NodeControlGraphReferenceRole.GRAPH_REVISION, value)
        except NodeControlContractError:
            pass
        else:
            return
    raise GraphAuthoringError("proposed_graph_id must be a bounded public graph reference")


@dataclass(frozen=True)
class SetDesiredGraphCommand:
    """Application command to publish desired graph truth for one workspace."""

    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None
    expected_desired_realized_projection_id: str | None = None
    expected_desired_graph_revision: int = 0
    actor_scopes: tuple[PolicyScope, ...] = ()
    explicit_secret_choices: Mapping[str, tuple[SecretVersionSelection, ...]] = field(default_factory=dict)

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
        if (type(self.actor_scopes) is not tuple
                or any(type(scope) is not PolicyScope for scope in self.actor_scopes)
                or not isinstance(self.explicit_secret_choices, Mapping)
                or len(self.explicit_secret_choices) > 1024):
            _selection_error()
        choices = {}
        for node_id, values in self.explicit_secret_choices.items():
            if (type(node_id) is not str or node_id not in self.graph.nodes
                    or type(values) is not tuple or not 1 <= len(values) <= 1024
                    or any(type(value) is not SecretVersionSelection for value in values)):
                _selection_error()
            if len({(value.reference, value.intent) for value in values}) != len(values):
                _selection_error()
            for value in values:
                SecretVersionSelection.__post_init__(value)
            choices[node_id] = tuple(sorted(values, key=lambda value: (value.reference.reference_id, value.intent.value)))
        object.__setattr__(self, "explicit_secret_choices", MappingProxyType(choices))


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
        secret_metadata_reader: SecretVersionMetadataReader | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._graph_id_factory = graph_id_factory
        self._clock = clock
        self._secret_metadata_reader = secret_metadata_reader
        self._issued_secret_preparations = {}
        self._secret_preparation_units = {}

    def set_desired_graph(
        self,
        command: SetDesiredGraphCommand,
    ) -> SetDesiredGraphResult:
        if not isinstance(command, SetDesiredGraphCommand):
            raise GraphAuthoringError("set_desired_graph requires SetDesiredGraphCommand")
        if command.explicit_secret_choices:
            with self._prepare_secret_selections(command) as prepared:
                with self._secret_selection_unit_of_work(prepared) as unit_of_work:
                    guard = unit_of_work.stores.graphs.lock_receiver_lifecycle(command.workspace_id)
                    result = set_desired_graph_in_unit_of_work(unit_of_work, prepared.command,
                        lifecycle_guard=guard, graph_id=self._graph_id_factory(), created_at=self._clock(),
                        _secret_preparation=prepared)
                    unit_of_work.commit()
                    return result
        with self._unit_of_work_factory() as unit_of_work:
            lifecycle_guard = unit_of_work.stores.graphs.lock_receiver_lifecycle(
                command.workspace_id
            )
            result = set_desired_graph_in_unit_of_work(
                unit_of_work,
                command,
                lifecycle_guard=lifecycle_guard,
                graph_id=self._graph_id_factory(),
                created_at=self._clock(),
            )
            unit_of_work.commit()
            return result

    @contextmanager
    def _prepare_secret_selections(self, command):
        if (type(command) is not SetDesiredGraphCommand
                or PolicyScope.SECRET_PROVIDER_USE not in command.actor_scopes
                or self._secret_metadata_reader is None):
            _selection_error()
        original_fingerprint = _secret_command_fingerprint(command)
        with self._unit_of_work_factory() as uow:
            workspace = uow.stores.workspaces.get(command.workspace_id)
            _require_desired_lineage(workspace, command)
            graph, documents = _author_secret_graph(uow.stores, workspace, command.graph,
                explicit=dict(command.explicit_secret_choices))
            facts = _admitted_selection_facts(uow.stores, command.workspace_id, graph, lock=False)
            source_fingerprint = _secret_source_fingerprint(uow.stores, workspace, documents)
        requests = []
        for node_id, selection, reference, provider in facts:
            if selection not in command.explicit_secret_choices.get(node_id, ()):
                continue
            request = SecretVersionMetadataRequest(command.workspace_id, command.actor_id,
                "smeta_" + _selection_digest((original_fingerprint, node_id, selection.descriptor())),
                provider.registration_id, reference.registration_id,
                provider.endpoint_reference, provider.credential_reference, selection)
            try:
                response = self._secret_metadata_reader.read_version(request)
                if type(response) is not SecretVersionMetadata:
                    _selection_error()
                SecretVersionMetadata.__post_init__(response)
                if (not response.available or response.correlation_id != request.correlation_id
                        or response.provider_registration_id != request.provider_registration_id
                        or response.reference_registration_id != request.reference_registration_id
                        or response.selection != request.selection):
                    _selection_error()
            except Exception:
                raise GraphAuthoringError("secret metadata observation is unavailable") from None
            requests.append(response)
        if original_fingerprint != _secret_command_fingerprint(command):
            _selection_error()
        admitted = replace(command, graph=DEFAULT_GRAPH_CODEC.decode(graph.descriptor()))
        prepared = _PreparedSecretSelection(self, admitted, _secret_command_fingerprint(admitted),
            source_fingerprint, facts, tuple(requests))
        self._issued_secret_preparations[id(prepared)] = prepared
        try:
            yield prepared
        finally:
            self._issued_secret_preparations.pop(id(prepared), None)

    @contextmanager
    def _secret_selection_unit_of_work(self, prepared):
        if (type(prepared) is not _PreparedSecretSelection or prepared.owner is not self
                or self._issued_secret_preparations.get(id(prepared)) is not prepared):
            _selection_error()
        with self._unit_of_work_factory() as uow:
            self._secret_preparation_units[id(uow)] = (uow, prepared)
            try:
                yield uow
            finally:
                self._secret_preparation_units.pop(id(uow), None)

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
    lifecycle_guard: object,
    graph_id: str,
    created_at: str,
    _secret_preparation=None,
) -> SetDesiredGraphResult:
    """Persist desired graph truth on the caller's transaction boundary."""

    if not isinstance(command, SetDesiredGraphCommand):
        raise GraphAuthoringError("set_desired_graph requires SetDesiredGraphCommand")
    if _secret_preparation is not None:
        _consume_secret_preparation(unit_of_work, command, _secret_preparation)
    elif command.explicit_secret_choices:
        _selection_error()
    if not unit_of_work.stores.graphs.owns_receiver_lifecycle(
        lifecycle_guard, command.workspace_id
    ):
        raise GraphAuthoringError("desired graph requires its transaction workspace guard")
    _validate_text(graph_id, "graph_id")
    _validate_text(created_at, "created_at")
    product_references = product_references_in_graph(command.graph)
    workspace = unit_of_work.stores.workspaces.get_for_update(
        command.workspace_id,
    )
    try:
        receiver_material = derive_receiver_bindings(command.workspace_id, graph_id,
            "proposed-identity", command.graph.descriptor())
        for graph, projection in ((workspace.current_graph_id, workspace.current_realized_projection_id),
                                  (workspace.desired_graph_id, workspace.desired_realized_projection_id)):
            receiver_material += _retained_receiver_material(unit_of_work.stores,
                command.workspace_id, graph, projection)
        if receiver_material:
            raise ReceiverLifecycleStorageError("receiver authoring requires its complete operation")
    except (ValueError, KeyError):
        raise GraphAuthoringError("receiver authoring requires its complete operation") from None
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
    selected_graph = admit_graph_secret_selections(unit_of_work.stores, workspace, command.graph,
        prepared=_secret_preparation)
    graph_version = GraphVersionRecord.from_graph(
        graph_id=graph_id,
        workspace_id=command.workspace_id,
        version=unit_of_work.stores.graphs.next_version_for_workspace(
            command.workspace_id
        ),
        graph=selected_graph,
        created_by=command.actor_id,
        created_at=created_at,
    )
    unit_of_work.stores.graphs.save(graph_version)
    realized_projection = unit_of_work.stores.realized_graphs.save(
        unit_of_work.stores.realized_graphs.identity_for_authored(
            command.workspace_id,
            graph_version.graph_id,
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


@dataclass(frozen=True, eq=False, repr=False)
class _PreparedSecretSelection:
    owner: GraphAuthoringService
    command: SetDesiredGraphCommand
    command_fingerprint: str
    source_fingerprint: str
    facts: tuple
    observations: tuple[SecretVersionMetadata, ...]


def _selection_digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        if len(encoded) > 1_048_576:
            _selection_error()
        return sha256(encoded).hexdigest()
    except (ValueError, TypeError, RecursionError):
        _selection_error()


def _secret_command_fingerprint(command):
    return _selection_digest(dict(workspace=command.workspace_id, actor=command.actor_id,
        graph=command.graph.descriptor(), expected=(command.expected_desired_graph_id,
            command.expected_desired_realized_projection_id, command.expected_desired_graph_revision),
        scopes=[scope.value for scope in command.actor_scopes],
        choices={key: [value.descriptor() for value in values]
            for key, values in command.explicit_secret_choices.items()}))


def _require_desired_lineage(workspace, command):
    if (workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision) != (
            command.expected_desired_graph_id, command.expected_desired_realized_projection_id,
            command.expected_desired_graph_revision):
        raise GraphAuthoringError("stale desired graph pointer")


def _previous_secret_graph(stores, workspace):
    if workspace.desired_graph_id is None:
        return DeploymentGraph("empty")
    record = stores.graphs.get(workspace.desired_graph_id)
    if record.workspace_id != workspace.workspace_id:
        _selection_error()
    return DEFAULT_GRAPH_CODEC.decode(record.graph_descriptor)


def _author_secret_graph(stores, workspace, graph, *, explicit=None, historical=False, lock_products=False):
    """Pure carry-forward using documents from the graph's own workspace."""
    try:
        needs_carry = bool(explicit) or any(node.secret_selections for node in graph.nodes.values())
        previous = _previous_secret_graph(stores, workspace) if needs_carry else DeploymentGraph("empty")
        documents = {}
        for candidate in (previous, graph):
            for reference in sorted(product_references_in_graph(candidate),
                    key=lambda value: (value.descriptor_sha256.value, value.identity.key)):
                read = (stores.registered_products.get_for_update if candidate is graph and lock_products
                    else stores.registered_products.get)
                registered = read(workspace.workspace_id, reference)
                if (candidate is graph and not historical
                        and registered.status is not RegisteredProductStatus.ACTIVE):
                    _selection_error()
                documents[(reference.identity.key, reference.descriptor_sha256.value)] = registered.descriptor_document
        ordered = tuple(documents[key] for key in sorted(documents))
        if not needs_carry:
            return graph, ordered
        result = carry_forward_secret_selections(previous, graph, explicit or {}, product_documents=ordered)
        # Embedded selected data never implicitly requests a provider lookup.
        for node_id, node in graph.nodes.items():
            for selection in node.secret_selections:
                overridden = any((choice.reference, choice.intent) == (selection.reference, selection.intent)
                    for choice in (explicit or {}).get(node_id, ()))
                if (type(selection) is SecretVersionSelection and not overridden
                        and selection not in result.nodes[node_id].secret_selections):
                    _selection_error()
        return result, ordered
    except (KeyError, ValueError, TypeError, AttributeError):
        raise GraphAuthoringError("secret selection preparation is invalid or unavailable") from None


def _admitted_selection_facts(stores, workspace_id, graph, *, lock):
    facts = []
    try:
        for node_id in sorted(graph.nodes):
            for selection in graph.nodes[node_id].secret_selections:
                if type(selection) is not SecretVersionSelection:
                    continue
                reference = (stores.secret_references.get_active_for_update(workspace_id, selection.reference)
                    if lock else stores.secret_references.get_active(workspace_id, selection.reference))
                provider = (stores.secret_providers.require_active_registration_for_update(
                    workspace_id, reference.provider_registration_id) if lock else
                    stores.secret_providers.get_by_registration(workspace_id, reference.provider_registration_id))
                if provider.status is not RegisteredSecretProviderStatus.ACTIVE:
                    _selection_error()
                _validate_reference_admission(reference, provider)
                if selection.intent not in reference.allowed_intents:
                    _selection_error()
                facts.append((node_id, selection, reference, provider))
    except (KeyError, ValueError, SecretProviderRegistrationError):
        raise GraphAuthoringError("secret selection authority is unavailable") from None
    return tuple(facts)


def _secret_source_fingerprint(stores, workspace, documents):
    return _selection_digest(dict(workspace=workspace.workspace_id,
        lineage=(workspace.desired_graph_id, workspace.desired_realized_projection_id,
            workspace.desired_graph_revision),
        previous=_previous_secret_graph(stores, workspace).descriptor(),
        products=[document.content_digest for document in documents]))


def _consume_secret_preparation(uow, command, prepared):
    if type(prepared) is not _PreparedSecretSelection or type(prepared.owner) is not GraphAuthoringService:
        _selection_error()
    owner = prepared.owner
    binding = owner._secret_preparation_units.get(id(uow))
    if (owner._issued_secret_preparations.get(id(prepared)) is not prepared
            or binding is None or binding[0] is not uow or binding[1] is not prepared):
        _selection_error()
    owner._issued_secret_preparations.pop(id(prepared))
    if _secret_command_fingerprint(command) != prepared.command_fingerprint:
        _selection_error()


def admit_graph_secret_selections(stores, workspace, graph, *, prepared=None, saved=False):
    """Shared guarded selected-state admission; this function performs no provider IO."""
    explicit = {} if prepared is None else dict(prepared.command.explicit_secret_choices)
    normalized, documents = _author_secret_graph(stores, workspace, graph,
        explicit=explicit, lock_products=True)
    result = graph if saved else normalized
    facts = _admitted_selection_facts(stores, workspace.workspace_id, result, lock=True)
    if prepared is not None and (facts != prepared.facts
            or _secret_source_fingerprint(stores, workspace, documents) != prepared.source_fingerprint):
        _selection_error()
    return result


def original_carried_secret_graph(stores, command):
    """Replay derives only from immutable original lineage, without fresh authority."""
    from types import SimpleNamespace
    original = SimpleNamespace(workspace_id=command.workspace_id,
        desired_graph_id=command.expected_desired_graph_id)
    return _author_secret_graph(stores, original, command.graph,
        explicit=getattr(command, "explicit_secret_choices", {}), historical=True)[0]


def product_reference_in_node(node: Node) -> ProductReference | None:
    """Extract one node's normalized, pinned product reference when present."""

    identity_value = node.metadata.get("product_identity")
    digest_value = node.metadata.get("product_descriptor_digest")
    if identity_value is None and digest_value is None:
        return None
    if not isinstance(identity_value, str) or not isinstance(digest_value, str):
        raise GraphAuthoringError(
            f"node {node.node_id!r} has malformed product reference metadata"
        )
    return ProductReference(
        identity=_product_identity_from_key(identity_value),
        descriptor_sha256=ProductDescriptorDigest(digest_value),
    )


def product_references_in_graph(graph: DeploymentGraph) -> tuple[ProductReference, ...]:
    """Extract pinned product references from product-instantiated graph nodes."""

    if not isinstance(graph, DeploymentGraph):
        raise GraphAuthoringError("product references require DeploymentGraph")
    references: set[ProductReference] = set()
    for node in graph.nodes.values():
        reference = product_reference_in_node(node)
        if reference is not None:
            references.add(reference)
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
