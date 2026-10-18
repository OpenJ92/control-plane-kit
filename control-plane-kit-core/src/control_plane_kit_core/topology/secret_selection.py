"""Pure successor authoring; supplied product documents confer no authority."""
from dataclasses import replace
from control_plane_kit_core.products import (
    ProductDescriptorCodec, ProductDescriptorDocument, ProductIdentity,
    ProductDescriptorDigest, OciImageReference,
)
from control_plane_kit_core.secret_selections import (
    SecretSelectionError, author_secret_selections, normalize_secret_selections,
    secret_selection_key,
)
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, _receiver_health_declaration_successor,
    select_receiver_node_control_configuration_artifact,
    select_receiver_node_control_configuration_slot,
)
from control_plane_kit_core.secrets import secret_delivery_sort_key
from control_plane_kit_core.topology.graph import DeploymentGraph

_COORDINATES = ("product_identity", "product_descriptor_digest", "oci_image")


def _refuse():
    raise SecretSelectionError("secret authoring compatibility is unverified")


def _coordinates(node):
    present = [key in node.metadata for key in _COORDINATES]
    if not any(present):
        return None
    if not all(present):
        _refuse()
    values = tuple(node.metadata[key] for key in _COORDINATES)
    if any(type(value) is not str or not 1 <= len(value) <= 2048 for value in values):
        _refuse()
    valid = False
    try:
        namespace, name, revision = values[0].split("/")
        identity = ProductIdentity(namespace, name, int(revision))
        ProductDescriptorDigest(values[1])
        registry, path = values[2].split("/", 1)
        repository, digest = path.split("@")
        image = OciImageReference(registry, repository, digest)
        valid = identity.key == values[0] and image.execution_reference == values[2]
    except (ValueError, TypeError, AttributeError, OverflowError):
        valid = False
    if not valid:
        _refuse()
    return values


def _documents(values):
    if type(values) is not tuple:
        _refuse()
    documents = {}
    valid = True
    try:
        for value in values:
            if type(value) is not ProductDescriptorDocument:
                _refuse()
            decoded = ProductDescriptorCodec().decode_document(value.content)
            if decoded.product != value.product:
                _refuse()
            key = decoded.product.identity.key, decoded.content_digest, decoded.product.image.execution_reference
            if key in documents:
                _refuse()
            documents[key] = decoded.product
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        _refuse()
    return documents


def _artifact_slots(values):
    return tuple(sorted((value.artifact_id, value.target_path, value.media_type, value.file_mode)
                        for value in values))


def _compatible_product(before, after, documents):
    old, new = _coordinates(before), _coordinates(after)
    if old == new:
        return True
    if old is None or new is None or old not in documents or new not in documents:
        _refuse()
    first, second = documents[old], documents[new]
    if (first.identity.namespace, first.identity.name, first.product_family) != (
            second.identity.namespace, second.identity.name, second.product_family):
        return False
    a, b = first.runtime_contract, second.runtime_contract
    return (_artifact_slots(a.configuration_artifacts) == _artifact_slots(b.configuration_artifacts)
            and replace(a, configuration_artifacts=b.configuration_artifacts) == b)


def _node_compatible(before, after):
    return (before.node_id == after.node_id and before.runtime_id == after.runtime_id
            and before.kind == after.kind and before.block_family == after.block_family
            and before.block_spec == after.block_spec and before.sockets == after.sockets
            and before.lifecycle == after.lifecycle
            and before.runtime_authority_deliveries == after.runtime_authority_deliveries
            and _artifact_slots(before.configuration_artifacts) == _artifact_slots(after.configuration_artifacts))


def _declaration_compatible(before, after, documents):
    """Carry an admitted choice through one exact declaration-only successor.

    Documents establish pure compatibility, not active registration. Complete
    graph/route admission and current verifier authority remain with their owners.
    """
    old, new = _coordinates(before), _coordinates(after)
    if old == new or old not in documents or new not in documents:
        return False
    first, second = documents[old], documents[new]
    if ((first.identity.namespace, first.identity.name) != (second.identity.namespace, second.identity.name)
            or first.identity == second.identity
            or replace(second, identity=first.identity, runtime_contract=first.runtime_contract) != first):
        return False
    a, b = first.runtime_contract, second.runtime_contract
    if a.control_surfaces == b.control_surfaces:
        return False
    codec = ReceiverNodeControlConfigurationCodec()
    try:
        defaults = tuple(select_receiver_node_control_configuration_slot(
            artifacts=value.configuration_artifacts, environment=value.public_environment,
            control_surfaces=value.control_surfaces) for value in (a, b))
        old_default, new_default = defaults
        if replace(new_default, content=old_default.content, source_digest=old_default.source_digest) != old_default:
            return False
        # Unchanged defaults may be opaque templates. Changed defaults preserve
        # their own identity/trust; neither default confers installed identity.
        if old_default != new_default:
            prior, next_value = (codec.decode_bytes(value.content.encode("utf-8")) for value in defaults)
            if (prior.declaration.surface != a.control_surfaces[0]
                    or next_value.declaration.surface != b.control_surfaces[0]
                    or not _receiver_health_declaration_successor(prior, next_value)):
                return False
        if replace(b, control_surfaces=a.control_surfaces, configuration_artifacts=tuple(
                old_default if value.artifact_id == new_default.artifact_id else value
                for value in b.configuration_artifacts)) != a:
            return False
        wrappers = tuple(select_receiver_node_control_configuration_artifact(
            artifacts=node.configuration_artifacts,
            environment=node.public_environment + node.socket_environment,
            control_surfaces=node.block_spec.control_surfaces) for node in (before, after))
        original, updated = (codec.decode_bytes(value.content.encode("utf-8")) for value in wrappers)
        if (before.block_spec.control_surfaces != a.control_surfaces
                or after.block_spec.control_surfaces != b.control_surfaces
                or original.target.node_id.value != before.node_id
                or original.target.runtime_id.value != before.runtime_id
                or not _receiver_health_declaration_successor(original, updated)):
            return False
        wrapper, successor = wrappers
        if replace(successor, content=wrapper.content, source_digest=wrapper.source_digest) != wrapper:
            return False
        restored = replace(after,
            block_spec=replace(after.block_spec, control_surfaces=before.block_spec.control_surfaces),
            metadata={**after.metadata, "product_identity": before.metadata["product_identity"],
                "product_descriptor_digest": before.metadata["product_descriptor_digest"]},
            configuration_artifacts=tuple(wrapper if value.artifact_id == successor.artifact_id else value
                for value in after.configuration_artifacts), secret_selections=before.secret_selections)
        return restored == before
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return False


def _slots(node, key):
    return tuple(sorted(secret_delivery_sort_key(value) for value in node.secret_deliveries
                        if hasattr(value, "intent") and secret_selection_key(value) == key))


def carry_forward_secret_selections(previous_graph, desired_graph, explicit_choices, *, product_documents=()):
    """Carry known choices across compatible revisions, without provider reads."""
    if type(previous_graph) is not DeploymentGraph or type(desired_graph) is not DeploymentGraph:
        _refuse()
    if type(explicit_choices) is not dict or not set(explicit_choices) <= set(desired_graph.nodes):
        _refuse()
    documents = _documents(product_documents)
    result = desired_graph
    for node_id, desired in desired_graph.nodes.items():
        normalize_secret_selections(desired.secret_deliveries, desired.secret_selections)
        _coordinates(desired)
        previous = previous_graph.nodes.get(node_id)
        inherited = ()
        if previous is not None:
            normalize_secret_selections(previous.secret_deliveries, previous.secret_selections)
            compatible = _compatible_product(previous, desired, documents)
            if ((compatible and _node_compatible(previous, desired))
                    or _declaration_compatible(previous, desired, documents)):
                inherited = tuple(value for value in previous.secret_selections
                                  if _slots(previous, secret_selection_key(value))
                                  == _slots(desired, secret_selection_key(value)))
        selections = author_secret_selections(desired.secret_deliveries, inherited,
                                               explicit_choices.get(node_id, ()))
        result = result.update_node(replace(desired, secret_selections=selections))
    return result


__all__ = ["carry_forward_secret_selections"]
