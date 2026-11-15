"""Public recipes over workspace-pinned products; no preview persistence/effects."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Mapping

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime, SocketConnection
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
from .products import RegisteredProductStatus
from .read_services._redaction import public_verification_shape


MAX_BYTES = 1048576
RECIPE_SCHEMA = "registered-product-topology.v1"
COMMITMENT_PROFILE = "registered-product-topology-public.v1"


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
            public_json_bytes(DEFAULT_GRAPH_CODEC.encode(graph))
            for instance in recipe.instances:
                node = graph.node(instance.role_id)
                if (node.metadata.get("product_identity") != instance.reference.identity.key
                        or node.metadata.get("product_descriptor_digest") != instance.reference.descriptor_sha256.value):
                    raise ValueError
            return recipe, graph
        except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise ProductTopologyAuthoringError() from None

    def preview(self, context, raw_recipe):
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
