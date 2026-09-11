Source: [control-plane-kit-core/src/control_plane_kit_core/products.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/products.py).
Maintain this document alongside its source file. Recheck descriptor admission,
configuration matching, imported socket/material contracts and consuming product
registration paths when this language changes.

This module owns pure external product identities, OCI image references, runtime
contracts, descriptor documents, an in-memory catalogue and instantiation into
ordinary application topology. Its 2209 lines contain several representation and
validation boundaries, not a registry client or container deployment service.
The useful composition is product contract plus instance configuration to
ApplicationBlock, followed by the existing topology compiler. Materialization
here constructs graph data; external interpreters perform approved effects later.

## Ownership and dependencies

The imported [algebra](../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py)
owns sockets, BlockSpec, ApplicationBlock and RuntimeContext. Products establishes
stronger socket-container admission and canonical ordering locally; it does not
change BlockSockets authoring order globally. Protocol and SocketBinding own the
protocol/binding language. Endpoint and LiteralAddress supply graph address
values, while verification owns check kinds and their expected protocols.

Environment owns validated public static bindings; configuration owns artifact
content, target and descriptor validation; lifecycle owns ownership/persistence
and data-resource values. Secret delivery variants, references and sorting belong
to [secrets.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py).
Process-authority deliveries and their normalization belong to runtime_authority,
not product descriptors. Node-control owns surface descriptors/codecs and its
count constant. Products composes those owners rather than resolving secrets,
authorizing runtime access, implementing control handlers or inventing lifecycle
execution. Imports remain within Core plus standard-library representation tools.

## Identity and OCI artifact values

ProductIdentity is namespace/name/positive exact-integer contract_revision.
Namespace and name are lowercase ASCII components separated by dots or hyphens,
at most 96 characters each. The revision has no explicit upper bound here.
IdentityCodec requires exactly those fields. require_unique_product_identities
checks typed members, rejects duplicates and returns sorted identities. It is
different from catalogue insertion, which accepts identical content again.

ProductDescriptorDigest validates 64 lowercase hex characters; constructing it
does not hash or retrieve content. ProductReference combines typed identity and
digest. from_document hashes document.content and uses document.product.identity;
its exact two-field codec delegates identity validation. A reference is neither
an image digest nor proof of workspace registration or provider ownership.

OciImageReference requires registry, repository and sha256-prefixed image digest.
The execution reference uses registry/repository@digest; an optional tag appears
only in the human reference. Registry grammar rejects schemes/userinfo and admits
a one-to-five-digit optional port, but applies no explicit total length or numeric
port-range bound. Repository is at most 255 characters of lowercase OCI-like
slash-separated components; tags are at most 128 characters. Image digest syntax
does not verify an artifact's existence, bytes, signature or publisher.

Platforms are typed OciPlatform values, converted to a sorted tuple. Platform
parts use the 96-character identity grammar; variant is optional. Duplicates are
not rejected. Ordering uses the generated dataclass comparison, including the
optional variant; this is not a general normalization of arbitrary platform
inputs. require_platform explicitly checks membership when constraints exist;
an empty tuple permits any typed requested platform. Instantiation/materialization
does not call this method or inspect the actual machine.

Provenance becomes sorted key/value pairs. Keys use identity grammar and reject
secret/token/password/credential/key substrings; values are strings at most 256
characters. These are finite metadata restrictions, not proof of redaction or
provenance authenticity. The codec's mapping path caps provenance at 16 entries;
direct construction does not enforce that count, and the helper does not apply
the 96-character identity-part limit to keys. Tuple input uses last value for a
repeated key. The OCI descriptor has exactly six fields, including null tag,
platform list and provenance mapping; nested platform fields are exact as well.

## Runtime contract composition

ProductRuntimeContract first requires exact BlockSockets, exact requirement and
provider tuples, exact socket member types and exact string names. These checks
precede name-dependent sorting. It rejects duplicate names within each direction,
allows the same name in both directions, and constructs a new container sorted
by name. No additional general socket-name grammar is imposed here.

Other collections are normalized and checked against their imported value types:
provider ports have unique provider names referencing declared sockets, with
exact integer ports 1..65535; public environment names are unique; configuration
artifact IDs and paths are independently unique; retained mounts have unique
resource IDs and paths; capabilities are typed, unique and sorted. Several of
these paths sort or inspect attributes before type checks. The clean hostile
socket boundary must not be generalized to every constructor argument.

Secret deliveries are sorted by the owning key and checked for membership in the
closed variant union. This contract does not itself reject duplicate node-level
secret targets or check all cross-category environment/mount collisions.
RequirementSocket separately owns its delivery uniqueness, target uniqueness and
public/secret environment separation. A declared secret reference grants no use.

Verification checks must reference existing providers with compatible protocols.
Lifecycle must be typed; its data-resource IDs cannot overlap configuration
artifact IDs, and every retained mount must name a lifecycle data resource. The
helper checks membership, not that each referenced resource has RETAINED policy,
that every data resource is mounted, or that artifact/mount paths never overlap.
The retained-path filter requires a normalized absolute container path and rejects
root, traversal, selected namespace descendants and docker.sock text. It is a
finite string policy, not filesystem isolation or a complete host-path policy.

At most 16 node-control surfaces may be supplied in a tuple. They must be typed,
unique by provider socket and sorted. NODE_CONTROLLABLE must be present exactly
when surfaces exist, and each surface must name an HTTP provider. The imported
surface value owns its own variable and wire bounds. These declarations do not
establish a running SDK implementation or authorize a control command.

Runtime-contract descriptors carry the declared sockets, ports, public material,
secret deliveries, mounts, capabilities, verification and lifecycle. Nonempty
control_surfaces adds one optional key; an explicitly empty list is rejected on
decode and omitted on encode. Requirement secret_deliveries accepts the older
absent-field form and the current field, while encoding omits it when empty.
The codec requires exact variant keys, delegates nested decoding, preserves local
ProductRuntimeContractError and broadly wraps other exceptions with a retained
cause. It supplies no aggregate document bound on its own.

ContainerServerProduct combines typed identity, image and contract with optional
display/description and ProductFamily SERVER or DATA_SERVICE. Text is nonblank,
NUL-free and limited to 128/1024 characters, with selected shell/host-path
fragments rejected. This is not arbitrary sensitive-text detection. The exact
descriptor kind is container-server; unknown fields/kinds fail. Nested errors
may retain causes. ProductFamily is a product classification: this module's
instantiation still returns ApplicationBlock for either family.

## Documents: codec admission versus value construction

ProductDescriptorCodec produces product.cpk.json with media type
application/vnd.cpk.product+json and schema control-plane-kit.product. The default
bound is 262144 bytes; a positive exact-integer max_bytes can replace it. Empty or
oversized content is rejected at the codec boundary. Canonical bytes use compact
json.dumps with ensure_ascii=True and descriptor insertion order. This is the
product format's own canonical representation, not the node-control RFC 8785
canonicalizer or a sort_keys=True format. Document SHA-256 hashes those bytes.

Bytes and text must already match compact parsed JSON and then the reconstructed
typed product descriptor byte for byte. Pretty printing, reordered fields and
noncanonical normalized collections therefore fail. Mapping input is semantic:
it is snapshotted, decoded and re-encoded into canonical order instead. The outer
document requires exactly schema and product; nested codecs enforce the language.

The mapping snapshot accepts exact JSON scalar types, finite floats, mappings
with exact string keys and exact lists/tuples. It copies containers, rejects
cycles/duplicate iterated keys, tracks depth (greater than 64 fails), cumulative
members and exact escaped-byte size against the configured budget. Integer width
is checked before decimal conversion. Hostile traversal/shape/budget exceptions
become one generic ProductDescriptorError outside the caught handler, without
retaining its cause/context. Mapping methods are still caller code being invoked;
this is not an arbitrary Python execution sandbox or a traversal time limit.

That snapshot guarantee is specific to Mapping input. Raw JSON uses json.loads
without the same explicit depth/member walker or custom duplicate-key hook;
byte-for-byte canonical comparison rejects ordinary duplicate-key source text.
Other JSON/nested-codec errors may retain causes, and canonical encoding is not
configured with allow_nan=False. Do not claim uniform diagnostic sanitation or
identical numeric/recursion admission across all entry points.

ProductDescriptorDocument itself checks only the product and bytes types. It does
not decode content, enforce size/canonicality or prove content matches product.
Its content_digest simply hashes supplied bytes. Use codec-created/admitted
documents at trust boundaries; constructing the dataclass is not that admission.
This also limits what ProductReference.from_document and catalogue typing prove.

## Catalogue and per-instance topology

ProductCatalog accepts typed documents and orders them by product identity.
Repeated identity with the same content digest retains the first document;
different content digest raises ProductCatalogConflict. add returns self for
identical content or a new catalogue for a new identity; merge repeatedly adds
without mutating either operand. Lookup is explicit and missing identities fail.
An empty catalogue as identity, idempotent addition and associative conflict-free merge are the
useful laws exercised by its tests. They assume admitted document inputs: the
catalogue does not revalidate product/content agreement. Its descriptor parses
stored bytes; catalogue content/digest have no independent aggregate size cap.
There is no remote discovery, persistence, loader or executable class-path field.

ProductInstanceConfiguration supplies public environment, configuration artifacts,
node secret deliveries, requirement-specific deliveries and process-authority
deliveries. The last collection delegates normalization to runtime_authority and
is not a field of ProductRuntimeContract. from_contract copies default material
and extracts requirement deliveries, leaving authority deliveries empty.
Requirement-specific duplicates are rejected by socket plus delivery-contract
key, even if their reference identities differ. Other configuration collections
are sorted/typed; matching against the contract happens during instantiation.

Matching compares ordered key tuples, not complete values:

- Public environment preserves names while allowing values to change.
- Configuration preserves artifact ID, target path, media type and mode while
  allowing content and its digest/source metadata to differ.
- Secret delivery preserves kind, target, intent, policy and path binding while
  allowing the reference to change; requirement delivery additionally preserves
  the requirement socket.

This is substitution within declared slots. Tuple multiplicity matters; errors
count missing/extra keys rather than printing configured values. It does not
authorize secret resolution or process-authority delivery. Public environment is
checked first, so rejection of a wholly empty configuration does not by itself
test missing artifacts or secrets. Configured requirement sockets are rebuilt
with their delivery values while preserving other socket fields.

instantiate_product first encodes a product document; instantiate_catalog_product
uses the selected catalogue document. Both validate lowercase-leading role IDs
of at most 63 characters and matching typed configuration, then construct a
BlockSpec carrying role/display/capabilities/control surfaces/verification and an
OciContainerProductImplementation. The latter's materialize ignores runtime and
returns ProductMaterializedBlock with endpoints, public/configuration/secret/
authority material, lifecycle and product identity/descriptor-digest/image metadata.
The materialized wrapper has mutable dictionaries despite being a frozen dataclass
and no constructor validation of its own.

An explicit provider port yields scheme://block-id:port; otherwise the planned
host is block-id-socket. PostgreSQL uses postgresql; other protocols select the
first sorted endpoint scheme. These are proposed private addresses, not DNS,
reachability, port publication or a health observation. No platform check, image
pull, mount, process creation or runtime-specific placement occurs here.

The [topology compiler](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py)
calls materialize and copies its values into a graph node. Its connection step
adds requirement secret deliveries to a connected consumer. An unconnected
requirement can therefore retain configured delivery declarations while active
node-level deliveries remain empty. Instantiation alone does not activate them.

## Consuming boundaries and evidence

Selected actual Operations consumers demonstrate why admission matters:
[cpk_server.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py)
decodes descriptor_document before invoking product import;
[postgres/product_store.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/product_store.py)
decodes stored document JSON on reload; and
[products.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/products.py)
requires RegisteredProduct.reference to equal the reference derived from its
document. These checks do not move workspace registration or transaction ownership
into Core, nor establish every possible caller's input provenance. The separately
reviewed runtime-effect material can retain a registered base reference while
carrying graph-specific product overlays; do not impose unconditional descriptor
digest equality on that different representation without reviewing its consumer.

Test navigation: [descriptor admission](../../../../../control-plane-kit-core/tests/test_product_descriptor.py),
[catalogue laws](../../../../../control-plane-kit-core/tests/test_product_catalog.py),
[OCI references](../../../../../control-plane-kit-core/tests/test_oci_image_reference.py)
and [instantiation](../../../../../control-plane-kit-core/tests/test_product_instantiation.py)
cover canonical/mapping examples, selected clean hostile-input rejections,
conflict/idempotence/merge, digest-versus-tag identity, explicit platform checks,
configuration substitution and ordinary graph propagation. Existing runtime
contract, identity/reference, container-product, hardening, external fixture and
pipeline test companions provide the remaining navigation and their evidence
limits. AST import/call guards are syntactic evidence, not a transitive purity
proof; constructing DockerRuntime in these tests does not contact Docker.

Author read depth: full 2209-line owner; fresh full descriptor 269, catalogue 133,
OCI 170 and instantiation 340-line suites/helpers; retained previously reviewed
test companions and imported secret/node-control/runtime-effect context; selected
actual algebra, compiler, Operations import/registration/reload paths. This is not
a new full audit of all imported owners or all product tests. No executable
validation, provider access, credential use, source/test change, persistence or
deployment was performed. No new security surface is introduced by this note.
The direct-value/codec and finite-filter limits above describe source boundaries,
not newly executed exploits or authorization to fix application code.
