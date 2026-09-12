Source: [control-plane-kit-operations/src/control_plane_kit_operations/runtime_authorities.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_authorities.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This language separates a workspace's admitted runtime authority from admission
of an access-delivery value. RegisteredRuntimeAuthority supports Docker through
LocalDockerSocketAuthority or RemoteDockerTlsAuthority. Local authority means
access supplied by the process environment; it carries no socket path. Remote
authority retains a tcp endpoint and three SecretReferences for CA certificate,
client certificate and client key. Neither variant probes Docker or proves the
process can use that authority.

Remote endpoint validation requires bounded tcp text with hostname and port,
rejects userinfo and selected secret-shaped substrings, and permits only empty
or slash path with no nonempty query/fragment. Secret fields must be typed
references. This validates representation, not TLS material, host trust, provider
permission or network reachability. Remote fields are omitted from repr;
descriptor redacts the endpoint while exposing credential-reference handles.
DockerRuntimeAuthorityCodec is deliberately a storage codec and includes the
actual endpoint. Its output is not the public descriptor contract.

RegisteredRuntimeAuthorityDelivery wraps Core RuntimeAuthorityAccessDelivery
with workspace, first admission attribution and active/revoked status. The
[selected Core language](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py)
has local socket mount, remote TLS secret files and cloud credential session
kinds. It sorts unique labeled secret references and forbids them for local
socket delivery; remote/cloud values do not themselves require a complete
provider-specific set. The registered delivery has no recipient node/process
identity. It admits a delivery value for an authority in a workspace; the desired
node topology separately chooses which process should receive it. Admission
therefore does not mean access has been delivered to any recipient.

_admitted_runtime_authority_deliveries validates an exact typed requested tuple
against the supplied admission snapshot. Empty requested input returns empty
without inspecting the remaining inputs. Each nonempty request must match the
runtime authority reference and exactly one admitted value with the same workspace,
active status and complete delivery equality. It performs no database/provider
read and does not itself reject duplicated requested values. Selected planning,
execution-admission and StartNode/ReconcileNode realization callers obtain the
requested deliveries from the desired node and use this helper. Snapshot equality
does not freeze admission state through a later external effect or prove delivery
actually happened.

Four command types carry a tuple containing only PolicyScope values. The service
checks distinct runtime-authority register/revoke and runtime-authority-delivery
register/revoke scopes before opening a UoW, then delegates to the appropriate
[store](postgres/runtime_authority_store.py.md) and requests commit. Supplied
scopes and admission actor text are not an authenticated principal; the composing
boundary must establish them. Registration does not inspect secret-provider
registrations or resolve credentials. Delivery storage requires an active
authority reference, but does not check variant compatibility or equality between
the delivery's secret handles and the authority's connection handles.

Authority IDs use rauth_ plus SHA-256 of Python repr over workspace, reference,
runtime kind and sorted storage-descriptor items. Thus remote endpoint/reference
material contributes to identity without being exposed in the ID. Delivery IDs
use radel_ plus SHA-256 of repr over workspace and the encoded delivery. These
are implementation-defined deterministic identities, not a language-neutral
canonical JSON signature. Factories derive them; direct record constructors
check identifier shape rather than recomputing them.

Records retain initial attribution and current active/revoked status. Revoking
an authority and revoking its delivery are distinct store operations; neither
service deletes rows, unmounts sockets, removes recipient files, stops processes
or revokes provider credentials. No separate revocation actor/time or activity
event is appended here. The UoW commits on successful exit after a commit request
and rolls back otherwise. No external effect or compensation is hidden inside
the transaction.

Local timestamp fields are only bounded nonempty text without control characters;
the store validates canonical UTC. Metadata is only a Mapping and is copied
directly into record descriptors, without deep freezing, bounding or redaction.
Registered delivery descriptors include their secret-reference handles; the
[read projection](read_services/authority_secrets.py.md) applies additional
key-based redaction. Storage codecs, record descriptors and projected reads have
different disclosure contracts. Exact-key codec errors can name rejected fields,
and imported parser errors may propagate; no universal exception sanitizer exists
in this module.

Read depth: full 674-line owner, full 595-line runtime/delivery store and
[701-line tests](../../tests/test_runtime_authorities.py.md); selected actual Core
reference/delivery constructors/codecs, three helper call sites, schema and read
projection, with previously full UoW/temporal contracts. No source, credential,
provider, database or runtime operations were performed, and no tests were run.
The documentation adds no new security surface or authority.
