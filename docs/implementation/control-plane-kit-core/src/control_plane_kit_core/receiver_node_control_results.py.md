Source: [receiver_node_control_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_node_control_results.py).
Maintain this companion with source and imported contract changes.

The V2 result is a product of actual originating request, declaration and the
existing four-way NodeControlResult sum. It does not duplicate state/evidence
semantics or fabricate an old graph-bound request. Its flat wire adds only
`profile` and `request_digest` to every historical outcome shape.

```python
producer = ReceiverNodeControlResultCodec(actual_request, installed_declaration)
wire = producer.encode(producer.result(outcome))
consumer = ReceiverNodeControlResultCodec(retained_expected_request, expected_declaration)
observed = consumer.decode(wire)
```

Producers must supply the validated request they processed. Consumers retain an
independent expected request. Decode requires and compares received profile,
request ID, operation and complete digest before decoding outcome semantics.
Missing or wrong emitter digest is never backfilled. Invalid originating requests
cannot produce correlated semantic outcomes; transport errors remain external.

Encode also compares complete canonical request digests against the retained
context. Generated Python value equality alone is insufficient: boolean and
numeric scalar/map values can compare equal while their canonical origins differ.
Historical equality stays unchanged; canonical numeric equivalents such as1 and
1.0 continue to share an encoding context. The emitted digest still comes from
the actual result request, never from the retained expectation.

Every use revalidates request/declaration and nested outcome semantics, including
forged objects. Declared variable and codec, operation/evidence matrix and stale
state refusal remain owned by NodeControlResultCodec. Dict/raw wire stays closed;
numeric canonical observation comes from the command family.

The unchanged16384-byte whole-result cap includes128 bytes of profile/digest
overhead. Historical-shaped content therefore has at most16256 bytes; some old
maximum read results no longer fit. Tests independently witness16384/16385 and
show the overflow is still a valid historical outcome. No outcome-specific cap
is expanded and historical wire bytes are unchanged.

The unkeyed digest correlates content. It proves neither responder identity,
execution, freshness nor exactly-once. No effect/history or downstream adoption
is included; whole #1881 remains the adopter gate.
