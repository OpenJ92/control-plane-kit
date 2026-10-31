Source: [product_directory.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/product_directory.py).
Maintain this companion alongside its source.

This private projection serves active workspace product pages and exact digest
detail through `InstanceReadService`. It preserves the backend-issued original
ProductReference and labels detail `noncanonical-product-display`; the display
cannot be inverted into a canonical descriptor. Pages have live membership,
not a cross-page snapshot, and serialized envelopes are capped at 1048576 bytes
with categorical failure rather than truncation.

The structural whitelist exposes declared interface, image, lifecycle and
configuration shapes. Core's `product_configuration_slots` combines embedded
artifact slots and content-free required slots, including v2 descriptors.
The `configuration` array means **declared configuration slots**: it does not
label default-versus-required origin or establish that material is missing.
File contents, default environment values, concrete secret handles and arbitrary
registration/image metadata are omitted. Descriptions remain untrusted prose;
registration and advertised capabilities do not prove health, pullability,
deployment eligibility or granted authority.
