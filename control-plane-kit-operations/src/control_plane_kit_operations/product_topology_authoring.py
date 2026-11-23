"""Public recipes over workspace-pinned products; no preview persistence/effects."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from typing import Mapping

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime, SocketConnection
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationArtifactRequirement
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.public_ingress import NamedPublicIngressCodec
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfiguration, ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_slot,
)
from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ProductCatalog, ProductInstanceConfiguration, ProductReferenceCodec, instantiate_catalog_product,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology, validate_graph
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck
from ._workload_verifier_authority import WorkloadVerifierAuthorityUnavailable, current_workload_verifiers
from .ingress_authorities import IngressAuthorityNotFound
from .products import ProductRegistrationNotFound, RegisteredProductStatus
from .read_services.receiver_authoring_context import (
    ReceiverAuthoringContextError, ReceiverAuthoringContextQuery, ReceiverAuthoringContextReadService,
)
from .desired_topology_drafts import bounded_draft_graph_descriptor
from .read_services._redaction import public_verification_shape


MAX_BYTES = 1048576
RECIPE_SCHEMA = "registered-product-topology.v1"
EXTENSION_SCHEMA = "registered-product-ingress-extension.v1"
COMMITMENT_PROFILE = "registered-product-topology-public.v1"
EXTENSION_COMMITMENT_PROFILE = "registered-product-ingress-extension-public.v1"


class ProductTopologyAuthoringError(ValueError):
    """Closed input, authority, or registered-product refusal."""
    def __init__(self, status=400):
        super().__init__("product topology proposal is invalid or unavailable")
        self.status = status


def public_json_bytes(value):
    """Versioned commitment encoding: sorted ASCII JSON, no spaces/NaN."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(encoded) > MAX_BYTES:
            raise ValueError
        return encoded
    except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
        raise ProductTopologyAuthoringError() from None


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ProductTopologyAuthoringError()
    return value


def _text(value, limit=512):
    if type(value) is not str or not 1 <= len(value) <= limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProductTopologyAuthoringError()
    return value


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value):
        raise ProductTopologyAuthoringError()
    return value


def _array(value, limit):
    if type(value) is not list or len(value) > limit:
        raise ProductTopologyAuthoringError()
    return value


@dataclass(frozen=True)
class _Instance:
    role_id: str
    reference: object
    configuration: ProductInstanceConfiguration


@dataclass(frozen=True)
class ProductTopologyRecipe:
    name: str
    runtime_id: str
    network_name: str
    instances: tuple[_Instance, ...]
    connections: tuple[SocketConnection, ...]

    @classmethod
    def decode(cls, value):
        public_json_bytes(value)
        try:
            value = _object(value, {"schema", "name", "runtime", "nodes", "connections"})
            if value["schema"] != RECIPE_SCHEMA:
                raise ValueError
            runtime = _object(value["runtime"], {"runtime_id", "kind", "network_name", "authority_ref", "management", "lifecycle"})
            if (runtime["kind"] != "docker" or runtime["authority_ref"] is not None or runtime["management"] is not None
                    or runtime["lifecycle"] != ResourceLifecycle.owned_ephemeral().descriptor()):
                raise ValueError
            nodes = _array(value["nodes"], 32)
            if not nodes:
                raise ValueError
            instances = []
            for node in nodes:
                node = _object(node, {"role_id", "product", "configuration"})
                config = _object(node["configuration"], {"public_environment", "configuration_artifacts", "secret_deliveries",
                    "requirement_secret_deliveries", "runtime_authority_deliveries"})
                if any(config[key] != [] for key in config if key != "public_environment"):
                    raise ValueError
                environment = []
                for binding in _array(config["public_environment"], 64):
                    binding = _object(binding, {"kind", "name", "value"})
                    if binding["kind"] != "public-static":
                        raise ValueError
                    environment.append(PublicStaticEnvironmentBinding(binding["name"], binding["value"]))
                if len({binding.name for binding in environment}) != len(environment):
                    raise ValueError
                instances.append(_Instance(_identifier(node["role_id"]), ProductReferenceCodec().decode(node["product"]),
                    ProductInstanceConfiguration(public_environment=tuple(sorted(environment, key=lambda b: b.name)))))
            roles = {instance.role_id for instance in instances}
            if len(roles) != len(instances):
                raise ValueError
            connections = []
            for connection in _array(value["connections"], 64):
                connection = _object(connection, {"edge_id", "provider_role", "provider_socket", "consumer_role", "requirement_socket", "protocol"})
                if connection["provider_role"] not in roles or connection["consumer_role"] not in roles:
                    raise ValueError
                connections.append(SocketConnection(
                    _identifier(connection["provider_role"]), _identifier(connection["provider_socket"]),
                    _identifier(connection["consumer_role"]), _identifier(connection["requirement_socket"]),
                    Protocol.from_descriptor(connection["protocol"]), _identifier(connection["edge_id"])))
            if len({connection.edge_id for connection in connections}) != len(connections):
                raise ValueError
            return cls(_text(value["name"]), _identifier(runtime["runtime_id"]), _identifier(runtime["network_name"]),
                tuple(sorted(instances, key=lambda i: i.role_id)), tuple(sorted(connections, key=lambda c: c.edge_id)))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
            raise ProductTopologyAuthoringError() from None

    def descriptor(self):
        return {"schema": RECIPE_SCHEMA, "name": self.name,
            "runtime": {"runtime_id": self.runtime_id, "kind": "docker", "network_name": self.network_name,
                "authority_ref": None, "management": None, "lifecycle": ResourceLifecycle.owned_ephemeral().descriptor()},
            "nodes": [{"role_id": instance.role_id, "product": instance.reference.descriptor(), "configuration": {
                "public_environment": [binding.descriptor() for binding in instance.configuration.public_environment],
                "configuration_artifacts": [], "secret_deliveries": [], "requirement_secret_deliveries": [],
                "runtime_authority_deliveries": []}} for instance in self.instances],
            "connections": [{"edge_id": edge.edge_id, "provider_role": edge.provider_role, "provider_socket": edge.provider_socket,
                "consumer_role": edge.consumer_role, "requirement_socket": edge.requirement_socket,
                "protocol": edge.protocol.descriptor()} for edge in self.connections]}


def public_product_topology_graph(graph):
    """Allowlist public structure; no fixed verification values or material."""
    return {"name": graph.name,
        "runtimes": {key: {"runtime_id": runtime.runtime_id, "kind": runtime.kind.value,
            "children": sorted(runtime.children), "metadata": {"network_name": runtime.metadata.get("network_name")}}
            for key, runtime in sorted(graph.runtimes.items())},
        "nodes": {key: {"node_id": node.node_id, "runtime_id": node.runtime_id, "kind": node.kind,
            "metadata": {field: node.metadata[field] for field in ("product_identity", "product_descriptor_digest", "oci_image")},
            "providers": {socket.name: {"protocol": socket.protocol.descriptor()} for socket in sorted(node.sockets.providers, key=lambda s: s.name)},
            "requirements": {socket.name: {"protocol": socket.protocol.descriptor(), "required": socket.required,
                "binding": socket.binding.value, "env_bindings": list(socket.env_bindings)} for socket in sorted(node.sockets.requirements, key=lambda s: s.name)},
            "block_spec": {"role_id": node.block_spec.role_id,
                "verification": public_verification_shape(node.block_spec.verification.descriptor())}}
            for key, node in sorted(graph.nodes.items())},
        "edges": {key: {"edge_id": edge.edge_id, "provider": {"role": edge.provider_role, "socket": edge.provider_socket},
            "consumer": {"role": edge.consumer_role, "requirement": edge.requirement_socket},
            "protocol": edge.protocol.descriptor(), "binding": edge.binding.value}
            for key, edge in sorted(graph.edges.items())}}


def public_proposal_commitment(recipe, graph):
    """Never hash unexposed fixed fields from the compiled graph."""
    payload = {"profile": COMMITMENT_PROFILE, "recipe": recipe.descriptor(),
        "graph_descriptor": public_product_topology_graph(graph)}
    return {"profile": COMMITMENT_PROFILE, "sha256": sha256(public_json_bytes(payload)).hexdigest()}


def _eligible(contract):
    if (contract.configuration_artifacts or contract.configuration_requirements or contract.secret_deliveries
            or contract.retained_data_mounts or contract.control_surfaces or contract.gateway_transit is not None
            or contract.lifecycle != ResourceLifecycle.owned_ephemeral()
            or any(requirement.secret_deliveries for requirement in contract.sockets.requirements)
            or any(type(check) is not HttpCheck for check in contract.verification.checks)):
        raise ProductTopologyAuthoringError()


def _connector_instance(stores, workspace_id, connector, runtime_id, graph, receiver_context, *, for_update=False):
    _object(connector, {"node_id", "product", "receiver_id", "provider_socket"})
    node_id = _identifier(connector["node_id"])
    target = NodeControlReceiverTargetCodec().decode({"workspace_id": workspace_id, "runtime_id": runtime_id,
        "node_id": node_id, "receiver_id": connector["receiver_id"], "provider_socket_name": connector["provider_socket"]})
    if node_id in graph.nodes or node_id in graph.runtimes:
        raise ProductTopologyAuthoringError(409)
    sources = (receiver_context.current, receiver_context.desired, receiver_context.pending_draft.source)
    if any(row.binding.receiver_id == target.receiver_id or
           (row.binding.runtime_id, row.binding.node_id, row.binding.provider_socket_name) ==
           (runtime_id, node_id, target.provider_socket_name.value)
           for source in sources if source is not None for row in source.receivers):
        raise ProductTopologyAuthoringError(409)
    if stores.graphs.receiver_introduction(workspace_id, target.receiver_id) is not None:
        raise ProductTopologyAuthoringError(409)
    pin = ProductReferenceCodec().decode(connector["product"])
    lookup = stores.registered_products.get_for_update if for_update else stores.registered_products.get
    registered = lookup(workspace_id, pin)
    if registered.status is not RegisteredProductStatus.ACTIVE or registered.reference != pin:
        raise ProductTopologyAuthoringError(409)
    contract = registered.descriptor_document.product.runtime_contract
    slot = select_receiver_node_control_configuration_slot(artifacts=contract.configuration_artifacts,
        environment=contract.public_environment, control_surfaces=contract.control_surfaces,
        requirements=contract.configuration_requirements)
    if not isinstance(slot, ConfigurationArtifactRequirement) or len(contract.control_surfaces) != 1:
        raise ProductTopologyAuthoringError()
    declaration = WorkloadNodeControlSurfaceDeclaration(contract.control_surfaces[0],
        WorkloadNodeControlSurfaceDeclarationProfile.V2)
    purposes = [DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ]
    if declaration.surface.health_reads:
        purposes.append(DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
    if declaration.surface.variables:
        purposes.append(DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
    families = current_workload_verifiers(stores.delegation_signing_keys, workspace_id, purposes)
    configuration = ReceiverNodeControlConfiguration(target, declaration, families)
    content = ReceiverNodeControlConfigurationCodec().encode_bytes(configuration)
    artifact = ConfigurationArtifact(slot.artifact_id, slot.target_path, slot.media_type, content.decode(), slot.file_mode)
    block = instantiate_catalog_product(ProductCatalog.from_documents((registered.descriptor_document,)),
        pin.identity, role_id=node_id, configuration=ProductInstanceConfiguration(
            public_environment=contract.public_environment, configuration_artifacts=(artifact,)))
    runtime = graph.runtimes[runtime_id]
    if runtime.kind.value != "docker":
        raise ProductTopologyAuthoringError()
    isolated = compile_topology(DeploymentTopology(graph.name, DockerRuntime(runtime_id=runtime_id,
        network_name=runtime.metadata["network_name"], children=(block,))))
    return isolated.node(node_id), {"artifact_id": slot.artifact_id, "target_path": slot.target_path,
        "media_type": slot.media_type.value, "file_mode": slot.file_mode.value, "sha256": sha256(content).hexdigest()}


def _named_ingress(stores, workspace_id, spec, connector_id, graph, runtime_id, *, for_update=False):
    _object(spec, {"ingress_id", "authority_ref", "registration_id", "hostname", "target", "exposure", "lifecycle"})
    ingress = NamedPublicIngressCodec().decode({"ingress_id": spec["ingress_id"], "hostname": spec["hostname"],
        "exposure": spec["exposure"], "lifecycle": spec["lifecycle"], "target": spec["target"],
        "authority_ref": {"reference_id": spec["authority_ref"]}, "connector_node_id": connector_id})
    binding = stores.ingress_authorities.require_active_for_hostname(workspace_id, ingress.authority_ref, ingress.hostname, for_update=for_update)
    if binding.registration_id != spec["registration_id"] or binding.authority.allowed_hostname_pattern != ingress.hostname:
        raise ProductTopologyAuthoringError(409)
    target = graph.node(ingress.target.node_id)
    if target.runtime_id != runtime_id or not any(socket.name == ingress.target.provider_socket
            and socket.protocol == Protocol.HTTP for socket in target.sockets.providers):
        raise ProductTopologyAuthoringError()
    return ingress


class ProductTopologyAuthoringService:
    """Canonical documents stay inside the workspace owner; preview never commits."""
    def __init__(self, unit_of_work_factory):
        self._uow = unit_of_work_factory

    def _prepare(self, context, raw_recipe, *, preview):
        scope = PolicyScope.INSTANCE_WORKSPACE_READ if preview else PolicyScope.INSTANCE_WORKSPACE_EDIT
        if type(context) is not TrustedCommandContext or scope not in context.granted_scopes:
            raise ProductTopologyAuthoringError(403)
        recipe = ProductTopologyRecipe.decode(raw_recipe)
        try:
            with self._uow() as uow:
                uow.stores.workspaces.get(context.workspace_id)
                blocks = []
                for instance in recipe.instances:
                    registered = uow.stores.registered_products.get(context.workspace_id, instance.reference)
                    if registered.workspace_id != context.workspace_id or registered.reference != instance.reference:
                        raise ValueError
                    if preview and registered.status is not RegisteredProductStatus.ACTIVE:
                        raise ValueError
                    _eligible(registered.descriptor_document.product.runtime_contract)
                    blocks.append(instantiate_catalog_product(ProductCatalog.from_documents((registered.descriptor_document,)),
                        instance.reference.identity, role_id=instance.role_id, configuration=instance.configuration))
            graph = compile_topology(DeploymentTopology(recipe.name, DockerRuntime(runtime_id=recipe.runtime_id,
                network_name=recipe.network_name, children=tuple(blocks) + recipe.connections)))
            validate_graph(graph).require_valid()
            bounded_draft_graph_descriptor(graph)
            for instance in recipe.instances:
                node = graph.node(instance.role_id)
                if (node.metadata.get("product_identity") != instance.reference.identity.key
                        or node.metadata.get("product_descriptor_digest") != instance.reference.descriptor_sha256.value):
                    raise ValueError
            return recipe, graph
        except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise ProductTopologyAuthoringError() from None

    def prepare_ingress_preview(self, context, recipe):
        self._validate_ingress_input(context, recipe)
        with self._uow().ingress_extension_snapshot() as stores:
            return self._ingress_graph(stores, context, recipe)

    @staticmethod
    def _validate_ingress_input(context, recipe):
        scopes = {PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.INGRESS_AUTHORITY_READ, PolicyScope.DELEGATION_KEY_READ}
        if type(context) is not TrustedCommandContext or not scopes.issubset(context.granted_scopes):
            raise ProductTopologyAuthoringError(403)
        public_json_bytes(recipe)
        _object(recipe, {"schema", "base", "runtime_id", "connector", "ingress"})
        if recipe["schema"] != EXTENSION_SCHEMA:
            raise ProductTopologyAuthoringError()
        base = _object(recipe["base"], {"draft_id", "revision", "graph_id"})
        _text(base["draft_id"])
        _text(base["graph_id"])
        revision = base["revision"]
        if type(revision) is not int or not 1 <= revision < 2**63:
            raise ProductTopologyAuthoringError()

    def _ingress_graph(self, stores, context, recipe, *, for_update=False):
        base = recipe["base"]
        draft_id, revision, graph_id = base["draft_id"], base["revision"], base["graph_id"]
        try:
            drafts = stores.desired_topology_drafts
            draft = drafts.get(context.workspace_id, draft_id)
            saved = drafts.revision(context.workspace_id, draft_id, revision)
        except KeyError:
            raise ProductTopologyAuthoringError(404) from None
        if draft.deleted_at is not None or draft.head_revision != revision or saved.graph_id != graph_id:
            raise ProductTopologyAuthoringError(409)
        record = stores.graphs.get(saved.graph_id)
        if record.workspace_id != context.workspace_id:
            raise ProductTopologyAuthoringError(409)
        graph = DEFAULT_GRAPH_CODEC.decode(record.graph_descriptor)
        runtime_id = _identifier(recipe["runtime_id"])
        runtime = graph.runtimes[runtime_id]
        connector = _object(recipe["connector"], {"node_id", "product", "receiver_id", "provider_socket"})
        ingress = _named_ingress(stores, context.workspace_id, recipe["ingress"], connector["node_id"], graph, runtime_id, for_update=for_update)
        receiver_context = ReceiverAuthoringContextReadService(self._uow).read(
            ReceiverAuthoringContextQuery(context.workspace_id, pending_draft={
                "draft_id": draft_id, "expected_head_revision": revision}), context=context,
            snapshot=stores.graphs.receiver_authoring_snapshot())
        if receiver_context.pending_draft.source.graph_id != graph_id:
            raise ProductTopologyAuthoringError(409)
        node, slot = _connector_instance(stores, context.workspace_id, connector, runtime_id, graph, receiver_context, for_update=for_update)
        extended = graph.add_node(node).add_public_ingress(ingress)
        extended = replace(extended, runtimes={**graph.runtimes,
            runtime_id: replace(runtime, children=(*runtime.children, node.node_id))})
        validate_graph(extended).require_valid()
        bounded_draft_graph_descriptor(extended)
        return extended, slot, receiver_context.expectation.descriptor()

    @staticmethod
    def _ingress_result(context, recipe, graph, slot, expectation):
        public = public_product_topology_graph(graph)
        public["public_ingresses"] = [ingress.descriptor() for ingress in graph.public_ingresses]
        result = {"workspace_id": context.workspace_id, "kind": "product-topology-preview",
            "recipe": recipe, "graph_descriptor": public, "receiver_configuration": slot,
            "receiver_lifecycle": expectation}
        profile = EXTENSION_COMMITMENT_PROFILE
        digest = sha256(public_json_bytes({"profile": profile, **result})).hexdigest()
        result["proposal_commitment"] = {"profile": profile, "sha256": digest}
        public_json_bytes(result)
        return result

    def validate_extension_append(self, command):
        self._validate_ingress_input(command.context, command.recipe)
        expected = _object(command.commitment, {"profile", "sha256"})
        if (expected["profile"] != EXTENSION_COMMITMENT_PROFILE or type(expected["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]) is None
                or command.receiver_lifecycle is None):
            raise ProductTopologyAuthoringError()
        base = command.recipe["base"]
        if base["draft_id"] != command.draft_id or base["revision"] != command.expected_head_revision:
            raise ProductTopologyAuthoringError(409)

    def prepare_locked_extension(self, stores, command):
        recipe, context = command.recipe, command.context
        try:
            graph, slot, expectation = self._ingress_graph(stores, context, recipe, for_update=True)
            observed = self._ingress_result(context, recipe, graph, slot, expectation)
            if (observed["proposal_commitment"] != command.commitment
                    or command.receiver_lifecycle.descriptor() != expectation):
                raise ProductTopologyAuthoringError(409)
            return graph
        except ProductTopologyAuthoringError:
            raise
        except ReceiverAuthoringContextError as error:
            raise ProductTopologyAuthoringError(error.status) from None
        except (IngressAuthorityNotFound, ProductRegistrationNotFound, WorkloadVerifierAuthorityUnavailable):
            raise ProductTopologyAuthoringError(409) from None
        except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise ProductTopologyAuthoringError() from None

    def preview(self, context, raw_recipe):
        if type(raw_recipe) is dict and raw_recipe.get("schema") == EXTENSION_SCHEMA:
            try:
                graph, slot, expectation = self.prepare_ingress_preview(context, raw_recipe)
                return self._ingress_result(context, raw_recipe, graph, slot, expectation)
            except ProductTopologyAuthoringError:
                raise
            except ReceiverAuthoringContextError as error:
                raise ProductTopologyAuthoringError(error.status) from None
            except (IngressAuthorityNotFound, ProductRegistrationNotFound, WorkloadVerifierAuthorityUnavailable):
                raise ProductTopologyAuthoringError(409) from None
            except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
                raise ProductTopologyAuthoringError() from None
        recipe, graph = self._prepare(context, raw_recipe, preview=True)
        value = {"workspace_id": context.workspace_id, "kind": "product-topology-preview",
            "recipe": recipe.descriptor(), "graph_descriptor": public_product_topology_graph(graph),
            "proposal_commitment": public_proposal_commitment(recipe, graph)}
        public_json_bytes(value)
        return value

    def prepare_saved_graph(self, context, raw_recipe, expected_commitment):
        if type(context) is not TrustedCommandContext or PolicyScope.INSTANCE_WORKSPACE_EDIT not in context.granted_scopes:
            raise ProductTopologyAuthoringError(403)
        expected = _object(expected_commitment, {"profile", "sha256"})
        if expected["profile"] != COMMITMENT_PROFILE or type(expected["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]):
            raise ProductTopologyAuthoringError()
        # Retained documents allow existing draft replay before NEW-publication
        # active-pin admission. This method does not publish or confer authority.
        recipe, graph = self._prepare(context, raw_recipe, preview=False)
        if public_proposal_commitment(recipe, graph) != expected:
            raise ProductTopologyAuthoringError(409)
        return graph
