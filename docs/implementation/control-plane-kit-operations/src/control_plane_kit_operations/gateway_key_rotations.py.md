Source: [gateway_key_rotations.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotations.py).
Maintain this companion alongside its source.

Request admission explicitly retains the four legacy purposes. Nominal typing
is checked before membership; health or future purposes receive a fixed domain
error before UOW access. A persistable signing key does not grant deployment
rotation or approval authority. Rotation SQL remains restricted to the old four.

The real old-transit request and approval path already created exact durable
subjects. Its row validator now recognizes that same legacy value, and a focused
test proves exact request replay, subject/digest identity and query-only current
schema reentry. This establishes durable consistency, not end-to-end transit
rotation deployment support. Health request tests prove refusal before writes.

Existing scopes, correlation identity, locking, transitions, approval ownership,
retry and retained history are unchanged. Ordinary signing-key activation overlap
is a separate existing lifecycle operation. No health generation, new network
exposure, credentials, provider calls or destructive rotation effects are added.
