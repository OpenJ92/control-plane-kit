Source: [receiver_control_surface_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_control_surface_read_results.py).
Maintain this companion with source and imported contract changes.

The V3 union has separate capabilities and status values bound to an exact
independently supplied receiver request and existing declaration. Profile V3
applies to both declaration versions. Correlation fields derive from the request
and declaration; target/context are bound through the full request digest.

Capabilities returns the exact declaration. Status returns a canonical sorted,
distinct subset of declared variable names and derives NONE/PARTIAL/COMPLETE
coverage using the existing semantic helpers. Declaration V1 retains wire key
`registry_coverage`; V2 retains `variable_registry_coverage`. Empty installed
variables, including a health-only declaration, mean NONE. These values do not
report health observations or variable state.

```python
codec = ReceiverControlSurfaceReadResultCodec(status_request, declaration)
result = codec.status_result(())
assert result.registry_coverage is NodeControlSurfaceRegistryCoverage.NONE
assert codec.decode(codec.encode(result)) == result
```

Constructors and codec operations validate the exact request/declaration and
nominal installed references. Encode reconstructs the result; codec context is
revalidated on use. Closed dict and canonical-byte methods refuse swapped kinds,
requests, profiles, declarations, dishonest coverage, private/runtime fields and
malformed input with bounded detached family errors.

Global caps remain capabilities16902 and status4811(V1)/4820(V2). The exact
capabilities envelope or complete declared subset determines a tighter context
cap. Global/context limits precede nested payload decoding; raw byte bounds
precede JSON parsing. Existing declaration/subset/coverage semantics are reused
directly; old requests/results are never synthesized as an adapter.

No observations are taken and no durable history is written. The result proves
structural correlation, not present permission, installation or network delivery.
Historical bytes remain unchanged; downstream adoption awaits whole #1881.
