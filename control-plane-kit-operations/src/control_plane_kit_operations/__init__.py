"""Durable control-plane operations package boundary."""

from __future__ import annotations

from control_plane_kit_core import DeploymentProgramStage

from .foundation import (
    OPERATIONS_PACKAGE_BOUNDARY,
    OperationsPackageBoundary,
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

__version__ = "0.1.0"

__all__ = [
    "DeploymentProgramStage",
    "CatalogueDescriptorSource",
    "DescriptorSourceCodec",
    "ImportProductDescriptorCommand",
    "InlineDescriptorSource",
    "OPERATIONS_PACKAGE_BOUNDARY",
    "OperationsPackageBoundary",
    "ProductRegistrationConflict",
    "ProductRegistrationError",
    "ProductRegistrationNotFound",
    "ProductRegistrationService",
    "RegisteredProduct",
    "RegisteredProductStatus",
    "RemoteDescriptorSource",
]
