"""Declare fixture slots without manufacturing a replacement instance payload."""
from dataclasses import replace

from control_plane_kit_core.configuration import ConfigurationArtifactRequirement
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from control_plane_kit_operations.products import RegisteredProduct


def required_document(document, artifact_ids=None):
    product = document.product
    contract = product.runtime_contract
    selected = tuple(value for value in contract.configuration_artifacts
        if artifact_ids is None or value.artifact_id in artifact_ids)
    requirements = tuple(ConfigurationArtifactRequirement(value.artifact_id,
        value.target_path, value.media_type, value.file_mode) for value in selected)
    return ProductDescriptorCodec().encode_document(replace(product, runtime_contract=replace(
        contract, configuration_artifacts=tuple(value for value in contract.configuration_artifacts
            if value not in selected), configuration_requirements=(*contract.configuration_requirements, *requirements))))


def required_registration(registered, artifact_ids=None):
    document = required_document(registered.descriptor_document, artifact_ids)
    selected = RegisteredProduct.from_document(workspace_id=registered.workspace_id,
        descriptor_document=document, source=registered.source, imported_by=registered.imported_by,
        imported_at=registered.imported_at)
    return replace(selected, status=registered.status, metadata=registered.metadata)


def bind_document(node, document):
    reference = ProductReference.from_document(document)
    return replace(node, metadata={**node.metadata, "product_identity": reference.identity.key,
        "product_descriptor_digest": reference.descriptor_sha256.value})


def required_health_documents(nodes, documents, selected):
    documents = {family: required_document(document) for family, document in documents.items()}
    from tests.health_receiver_trust_fixture import NODES
    for family, document in documents.items():
        node_id = NODES[family]
        nodes[node_id] = bind_document(nodes[node_id], document)
    return nodes, documents, selected
