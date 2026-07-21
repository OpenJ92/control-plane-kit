"""Pure deployment language used before operational execution."""

__version__ = "0.1.0"

from control_plane_kit_core.products import (
    DuplicateProductIdentity,
    ProductIdentity,
    ProductIdentityCodec,
    ProductIdentityError,
    require_unique_product_identities,
)

__all__ = [
    "DuplicateProductIdentity",
    "ProductIdentity",
    "ProductIdentityCodec",
    "ProductIdentityError",
    "require_unique_product_identities",
]
