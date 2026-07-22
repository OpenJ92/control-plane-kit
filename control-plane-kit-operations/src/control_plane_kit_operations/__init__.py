"""Durable control-plane operations package boundary."""

from __future__ import annotations

from control_plane_kit_core import DeploymentProgramStage

from .foundation import (
    OPERATIONS_PACKAGE_BOUNDARY,
    OperationsPackageBoundary,
)
from .graph_authoring import (
    GraphAuthoringError,
    GraphAuthoringService,
    SelectableProduct,
    SetDesiredGraphCommand,
    SetDesiredGraphResult,
    product_references_in_graph,
)
from .products import (
    CatalogueDescriptorSource,
    DescriptorSourceCodec,
    ImportProductDescriptorCommand,
    InlineDescriptorSource,
    ProductRegistrationConflict,
    ProductRegistrationError,
    ProductRegistrationNotFound,
    ProductRegistrationService,
    RegisteredProduct,
    RegisteredProductStatus,
    RemoteDescriptorSource,
)
from .records import (
    GraphVersionRecord,
    OperationsRecordError,
    WorkspaceRecord,
)

__version__ = "0.1.0"

__all__ = [
    "DeploymentProgramStage",
    "CatalogueDescriptorSource",
    "DescriptorSourceCodec",
    "GraphAuthoringError",
    "GraphAuthoringService",
    "GraphVersionRecord",
    "ImportProductDescriptorCommand",
    "InlineDescriptorSource",
    "OPERATIONS_PACKAGE_BOUNDARY",
    "OperationsPackageBoundary",
    "OperationsRecordError",
    "ProductRegistrationConflict",
    "ProductRegistrationError",
    "ProductRegistrationNotFound",
    "ProductRegistrationService",
    "RegisteredProduct",
    "RegisteredProductStatus",
    "RemoteDescriptorSource",
    "SelectableProduct",
    "SetDesiredGraphCommand",
    "SetDesiredGraphResult",
    "WorkspaceRecord",
    "product_references_in_graph",
]
