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
            if compatible and _node_compatible(previous, desired):
                inherited = tuple(value for value in previous.secret_selections
                                  if _slots(previous, secret_selection_key(value))
                                  == _slots(desired, secret_selection_key(value)))
        selections = author_secret_selections(desired.secret_deliveries, inherited,
                                               explicit_choices.get(node_id, ()))
        result = result.update_node(replace(desired, secret_selections=selections))
    return result


__all__ = ["carry_forward_secret_selections"]
