Source: [product_topology_authoring.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/product_topology_authoring.py).
Maintain this companion alongside its source. Governing CPK #2091.

`ProductTopologyRecipe` is the closed `registered-product-topology.v1` public
recipe. One unbound Docker context, 1–32 pinned product instances, at most 64
socket connections, explicit public static environment settings and empty
material/configuration/runtime-delivery collections are supported. Products
requiring configuration, secret deliveries, retained mounts, control surfaces,
gateway transit or non-HTTP verification are refused. Core owns product input
validation, socket derivation, compilation and semantic graph validation.

`ProductTopologyAuthoringService` checks read (preview) or edit (save preparation)
before storage. It resolves each complete workspace reference, instantiates the
original retained document in a singleton Core catalogue, and checks the output
pin. Preview requires active registrations and never commits, allocates IDs or
calls providers. Save preparation permits retained revoked documents so the
existing draft owner's historical replay remains first; new publication still
requires active pins, current edit authority, session and receiver admission
inside that owner's transaction. Nothing is selected or deployed here.

Request, compiled graph, and preview response are each capped at 1 MiB. The
public proposal preimage is `{profile, recipe, graph_descriptor}`. `recipe` is
normalized by role, edge and environment name; the graph is an explicit public
allowlist of topology/runtime identities, product/image pins, socket shape,
edges and verification kind/check ID/provider socket. Paths, expected bodies,
health policy, endpoints and fixed settings are omitted. Encoding is JSON with
sorted keys, compact comma/colon separators, ASCII escaping, `allow_nan=False`,
then ASCII bytes. SHA-256 is returned as `{profile, sha256}`, where profile is
`registered-product-topology-public.v1`. It identifies the public proposal and
does not attest every stored graph byte. Save compares this commitment and
passes the private graph to the existing draft-create service; its four-field
receipt and internal intent/replay fingerprint are unchanged.

Operations' framework-neutral HTTP/MCP-shaped requests share this service.
Actual transport decoding and lost-reply handling at the calling boundary,
installed adoption and runtime execution remain separately owned and unproven.
