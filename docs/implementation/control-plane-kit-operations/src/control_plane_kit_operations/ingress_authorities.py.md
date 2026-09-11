Source: [control-plane-kit-operations/src/control_plane_kit_operations/ingress_authorities.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/ingress_authorities.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner contains ingress-authority admission, owned Cloudflare resource
receipts, pure teardown/token-delivery plans and generated-secret reference
evidence. These are distinct objects: a registered authority is local permission
metadata; an ownership receipt records IDs and attribution; a plan describes
actions; a custody projection retains reference/version evidence. None of their
constructors or services provisions a tunnel, creates DNS, reads a credential,
checks Cloudflare permission or proves public reachability.

CloudflareZoneIngressAuthority is the sole provider variant. It names account,
zone, API-token reference, allowed hostname pattern and the provider-registration
ID/reference prefix intended for generated secrets. Hostnames use lowercase DNS
labels; patterns authorize exactly one label beneath the configured zone. They
may be exact or have one non-whole-label wildcard. Matching substitutes one or
more alphanumeric/hyphen characters for the wildcard and validates the complete
hostname first. Thus a wildcard does not span labels or match an empty segment.
This is a local textual policy, not DNS ownership or provider token scope.

Authority fields require typed SecretReferences, not token bytes. The strict
codec's storage form and direct descriptor expose account/zone/hostname policy
and opaque references. Only api_token_ref is hidden from the dataclass repr;
the descriptor deliberately contains that handle. The
[read projection](read_services/authority_secrets.py.md) adds key-based redaction.
Ordinary bounded identifiers may contain benign secret-shaped words, as the
tests demonstrate; this is not arbitrary secret-value scanning. Metadata is only
a Mapping and is copied directly into registered-record descriptors.

RegisteredIngressAuthority has active/revoked status and first admission
actor/time. Its factory derives iauth_ plus SHA-256 of Python repr over workspace,
authority reference and the encoded authority; direct construction does not
recompute the ID. Register/revoke commands require a typed tuple of PolicyScope,
and the service checks their distinct ingress-authority scopes before opening
a UoW. Supplied scopes/actor strings must be authenticated by the composing
boundary. The service calls the [store](postgres/ingress_authority_store.py.md)
and requests commit; it does not validate active provider/secret registrations.
Revocation changes local selection status, without deleting DNS/tunnels or
revoking the external API token.

CloudflareOwnedIngressResource combines workspace/runtime/ingress/authority
coordinates, exact tunnel/DNS IDs, hostname/zone, source run/activity/event,
epoch, times and status. Its allocating/active/removing/removed/uncertain/orphaned
status is Operations history, distinct from Core's ephemeral/retained/external
ownership policy. Removed status requires both removal time and run ID; other
statuses reject either removal field. Run/activity identities use the actual Core
grammars. Local times are bounded text; canonical UTC admission belongs to the
store. Checking the fixed descriptor's keys for secret markers does not prove
the values are safe or the ownership claim is true at Cloudflare.

cloudflare_ingress_teardown_plan requires a typed authority/resource, matching
zone, an allowed hostname and a tunnel name beginning cpk-. These checks precede
the lifecycle decision: retained/external yields a skip action, ephemeral yields
delete-DNS-record followed by delete-tunnel using the recorded IDs. The helper
does not query current registration, compare a workspace/authority reference,
check resource status/freshness or attest provider ownership. It is not an
approval or executor. The plan dataclass has no independent post-init validator,
and an action's resource_id is optional; the checked factory is stronger than
arbitrary direct plan construction.

cloudflare_tunnel_token_delivery_plan checks zone/hostname and creates an explicit
TUNNEL_TOKEN SecretEnvironmentDelivery with CLOUDFLARE_TUNNEL_TOKEN intent.
Its plan fixes allocate-ingress, record-token-secret, start-connector ordering.
The plan value does not perform those steps, inspect a graph recipient, or link
the supplied secret handle to a persisted custody receipt/prefix. The shared
delivery validator checks type, environment name and SecretReference, not the
specific intent; the factory supplies the correct intent. The connector helper
requires exactly one matching TUNNEL_TOKEN delivery within a tuple, but ignores
unrelated entries rather than validating the entire tuple as a closed contract.

record_generated_ingress_secret projects a typed
[Core custody receipt](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
into workspace/purpose/reference, provider and reference registration IDs,
custody/version evidence and source attribution. It does not require the receipt
status to be active or match a custody grant, and the receipt itself has no
workspace field to compare. The projection validates shapes, not provider custody,
reference admission, source-event existence or successful token delivery. It
retains no raw token material.

The services request transactional commits; stores and external interpreters
have separate responsibilities. Authority records retain first attribution and
current status without a separate revocation event. Resource/secret receipts
carry source coordinates, but this module does not append activity events or
implement cross-provider compensation. Codec errors may expose rejected field
names or imported causes; bounded values and public projections are not a
universal logging policy.

Read depth: full 941-line owner, full 845-line combined store and
[1,097-line tests](../../tests/test_ingress_authorities.py.md); selected actual
Core ingress/reference/lifecycle, custody/environment delivery and run/activity
contracts, schema and projection, full small shared redactor and previously full
UoW/temporal codecs. Source remains at 087a892. No tests, credentials, providers,
database operations or runtime changes accompanied this documentation.
