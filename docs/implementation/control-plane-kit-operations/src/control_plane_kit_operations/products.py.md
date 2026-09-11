Source: [control-plane-kit-operations/src/control_plane_kit_operations/products.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/products.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner defines durable workspace product registration and image-pull
authority reference values, their commands and small transaction-owning services.
RegisteredProduct pairs a ProductReference with a descriptor document, acquisition
evidence, first import attribution, active/revoked status and metadata.
RegisteredImagePullAuthority pairs workspace admission with a scoped Core
ImagePullAuthority and the same two lifecycle statuses. These values describe
admitted operational records; they do not establish image existence, successful
pulls, credential validity or runtime compatibility.

DescriptorSourceEvidence is a closed sum of inline, remote descriptor URL and
catalogue URL evidence. DescriptorSourceCodec checks exact fields for each kind.
Remote/catalogue URLs must be bounded text with HTTPS and a nonempty netloc,
without userinfo, a nonempty query or a nonempty fragment. Optional expected
digests have the Core digest shape, and catalogue evidence carries a typed
ProductIdentity. These constructors do not fetch URLs, compare an expected hash
against acquired bytes, or compare the catalogue's identity with the admitted
document. A source value records a claim about acquisition, not proof of it.
URL paths and other accepted text are not scanned for arbitrary secret values;
the URL restrictions are not a network destination policy.

Product registration derives rprod_ plus SHA-256 of workspace, a NUL separator
and descriptor-content digest. The reference includes both product identity and
that digest. RegisteredProduct checks reference equality with the supplied
document's product identity and content hash. In the selected
[Core product contracts](../../../../../control-plane-kit-core/src/control_plane_kit_core/products.py),
ProductDescriptorDocument itself checks only the product and bytes types. The
codec is the boundary that validates and canonicalizes the descriptor language
and applies its default 262,144-byte limit. Neither the command nor this record
independently decodes a manually assembled document to prove its bytes describe
its product value. The store decodes retained content when reading it back;
normal callers should supply codec-produced documents.

The OCI reference carried by a product is separately digest-pinned; its optional
tag is a human label. Descriptor-content digest and OCI image digest identify
different objects. Core's runtime contract expresses declared sockets and other
runtime requirements; admitting that contract does not run its verification or
prove the image implements it. The Operations harness selects Core from the same
checkout, rather than an independently selected latest SDK pin.

Image-pull authority IDs hash workspace, registry, optional repository and
credential reference using NUL separators. Core
[ImagePullAuthority](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
accepts a credential reference value or converts a reference string. Its permits
predicate covers an exact registry and either all repositories, one repository,
or slash-delimited descendants of that repository. This is a scope predicate,
not a credential resolution or registry authorization result. Registration does
not link an authority to a particular product, resolve the reference, or require
a live secret-provider admission here.

Commands validate by constructing the corresponding candidate. The services
then call their store inside one unit of work and request commit; revoke follows
the same transaction shape. There are no PolicyScope checks or authenticated
principal objects in these services: caller authentication and mutation
authorization must be supplied by the composing surface. The
[product store](postgres/product_store.py.md) and
[image-pull authority store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/image_pull_authority_store.py)
preserve existing matching records, including revoked status and original
attribution. Sequential same-identity/scope replacement conflicts are checked
before insertion. Neither service implements an explicit replacement command,
reactivation or a concurrency protocol around those checks.

Revocation changes selection status while retaining the record. It does not
delete images, descriptors or credentials, stop workloads, rewrite graph
references, or revoke an external registry token. These services do not append
separate operation sessions, actions or activity events; the retained record has
initial admission actor/time and current status, without revocation actor/time.
The unit of work commits on successful exit after a commit request and rolls
back otherwise. There is no provider effect needing compensation in this owner.

Local identifier/time checks enforce nonempty text of at most 512 characters
without characters below ASCII 32; they are not calendar or whitespace-only
rejection. Store admission validates canonical UTC before its own lookups, even
on matching replay. Direct record construction validates identifier shape but
does not recompute its deterministic registration/authority ID. Metadata need
only be a Mapping: these frozen dataclasses do not deeply freeze, bound or redact
its contents. Codec errors can include unknown field names or chained imported
errors. These boundaries should not be read as universal safe-logging guarantees.

Read depth: full 475-line owner, full 232-line product store and
[383-line tests](../../tests/test_registered_products.py.md), full 186-line
image-pull authority store; selected actual Core identity/reference/document/
codec/OCI/runtime-contract and pull-authority definitions, current-schema
constraints and suite dependency wiring, with previously read full UoW/temporal
contracts. Documentation only: no source, credential, database, registry, test
or runtime action occurred, and no new security surface is introduced.
