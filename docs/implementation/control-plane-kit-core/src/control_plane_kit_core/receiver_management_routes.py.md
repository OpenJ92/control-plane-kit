Source: [receiver_management_routes.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_management_routes.py).
Maintain this companion with source and imported contract changes.

`ReceiverManagementRouteBinding(target_id, target, declaration, origin)` and
`ReceiverManagementRoutes(gateway_target, targets)` describe the common receiver
management route language. `ReceiverManagementRoutesCodec` reads/writes mappings
and bytes. The public names describe shared protocol values; the retained wire
profile remains `cpk-gateway-health-relay-configuration.v2`.

The language is promoted from accepted Servers `71cbf5b095cad9852db07a99da6b26ab48d9e2cc`
`health_relay_configuration.py` (unchanged at `93d2c8a`). Encoding retains
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)` and UTF-8;
it deliberately does not substitute another canonical JSON implementation.
Decode accepts noncanonical whitespace/order and emits the original canonical
form. Bindings sort by target ID, are unique by both alias and full receiver,
and share the gateway's workspace/runtime. Limits remain 128 entries, 131072
encoded/input bytes and eight nested JSON levels. Unknown/duplicate fields,
nonfinite/float values, invalid UTF-8 and unknown profiles refuse.

Nested receiver and v2 declaration values use their existing strict codecs.
Declaration/socket equality is structural. The origin is an explicit HTTP
hostname and numeric port in its exact canonical spelling; HTTPS, paths,
credentials, IPv6 literals, uppercase host spellings and normalized aliases
remain outside this existing protocol. This is a private-origin syntax, not
network authorization or evidence of a particular runtime endpoint.

Construction and encoding revalidate nested nominal values. Both encoder
methods also retain the old public artifact producer's depth/type check: a
constructible mixed declaration can exceed the route envelope's depth-eight
limit and must then refuse on encode as well as decode. Constructors keep
their existing semantics. The fixed error
retains the predecessor text and has no candidate attributes or chained
exception. Reprs omit route material. Decoding a value creates no effect,
authority or installation/health evidence.

Example for existing typed receivers and declaration:

```python
routes = ReceiverManagementRoutes(gateway_target, (
    ReceiverManagementRouteBinding("self", gateway_target, gateway_declaration,
                                  "http://gateway:8000"),
    ReceiverManagementRouteBinding("x", workload_target, workload_declaration,
                                  "http://workload-x:8080"),
))
content = ReceiverManagementRoutesCodec().encode_bytes(routes)
```

The codec does not require self membership or derive the graph's expected route
set; empty/subset documents are valid representations. Operations owns exact
graph-derived origin/membership and allowed deltas. Servers owns artifact IDs,
paths, environment/loading, runtime-contract projection and actual relay use.
#245 adopts this implementation and removes duplicate product semantics; this
child does not import Servers or alter planner/execution guards.
