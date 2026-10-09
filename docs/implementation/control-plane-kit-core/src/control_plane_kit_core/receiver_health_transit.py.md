Source: [receiver_health_transit.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_health_transit.py).
Maintain this companion with source and imported contract changes.

The explicit `gateway-node-health-read-transit-grant.v2` binds the complete new
health request/context, original attempt and interval, and complete gateway
own-receiver target. Workspace and runtime must agree between workload and
gateway for this supported local path. The old gateway_node_id and sibling
runtime fields are absent. Existing health transit V1 remains unchanged.

The gateway target's provider socket names its configured common own receiver,
not a transit endpoint. Different gateway-own and workload socket names are
valid. This value does not carry or approve the graph's transit socket, protocol,
ingress or management path. External owners must independently retain those
checks. Derived `gateway:workspace:node` audience text is routing, not complete
receiver identity; the verifier checks the entire independent gateway target.

Local expected gateway/workload scope, admitted request, declaration, kind,
issuer/key/attempt and time are validated before claims. A malformed caller
context raises a categorical error. Incoming grant type/purpose/malformed-value
refusals precede issuer/key/time/attempt/gateway; then the shared health checks
compare request to local scope/declaration/kind and grant to request scope,
authority context, kind, declaration and exact request ID/digest. A gateway or
attempt change alone does not create a fresh observation.

Strict dict/canonical-byte codecs reconstruct nominal values and require the
supplied audience to equal the derived value. Errors are fixed and detached;
no candidate material is retained. The unchanged aggregate cap is 2423 bytes,
derived audience cap 265, and lifetime 300 seconds. Independent fixtures reach
the exact cap despite the larger shape, then reject the first overflow.

The public digest is a distinct successor transit identity. Tests cover every
own receiver field, cross-workspace/runtime refusal, independent local inputs,
context and request substitution, ordered multi-mismatch refusal, forged/missing
fields, purpose/profile separation, canonical bytes and interval edges. This
pure predicate does not verify signatures, retain replay state, prove installation
or establish current approved-attempt membership. Downstream adoption remains
blocked by the complete Core #1881 aggregate.
