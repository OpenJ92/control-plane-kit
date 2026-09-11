Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/product_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/product_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

RegisteredProductStore retains one descriptor-content identity per workspace on
a supplied PostgreSQL connection. The
[registration service](../products.py.md) owns the surrounding unit of work and
commit request. This store makes no registry request, descriptor fetch, image
pull or runtime change. Its active/revoked status controls local product
selection, not the availability or lifecycle of external artifacts.

register constructs a RegisteredProduct candidate and encodes imported_at as
canonical UTC before its first lookup. It first looks for the same workspace
and content digest; a match returns the stored record without replacing its
source, import actor/time, metadata or status. A revoked match remains revoked.
The new candidate and timestamp still need valid admission, so replay is not a
validation bypass. For an unseen digest it scans active workspace records for
equal ProductIdentity and raises the explicit-replacement conflict if one is
found. Once an old record is revoked, a different digest can pass that active
check, while the old descriptor remains retained.

The insertion stores product-reference JSON, descriptor digest, parsed descriptor
JSON, exact UTF-8 descriptor content, source evidence, import attribution, active
status and empty metadata. Parsing content with json.loads here is not the Core
descriptor codec. A caller-supplied ProductDescriptorDocument is not independently
decoded before insertion; the document constructor and registration candidate do
not prove product-value/content coherence. Codec-produced input supplies that
boundary on the normal path. No comparison of source expected hashes or catalogue
identity against this document occurs here.

The selected
[schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
enforces primary registration ID, unique workspace/digest, a workspace foreign
key, lowercase digest shape, active/revoked status and equality of the stored
digest with SHA-256 of descriptor_content encoded as UTF-8. It does not add an
active ProductIdentity uniqueness constraint or equate every retained JSON copy.
The same-identity policy uses plain reads without advisory/row locks, an upsert
or a serialization retry. Concurrent different-digest imports can therefore pass
the same pre-insert policy check; concurrent equal-digest inserts can meet a
database uniqueness error rather than the sequential replay return. The file
does not translate such driver errors into ProductRegistrationConflict.

get scopes by workspace/digest, decodes the row and then requires complete
reference equality. list_active filters status and orders by descriptor digest;
it loads the full active set without a page limit. The active-identity scan calls
that selector and can fail on another malformed retained row. revoke first reads
the row, returns an already-revoked receipt unchanged, otherwise updates status
and rereads it. No descriptor or external image is deleted; no revocation actor,
time or activity event is appended in this store.

Row decoding uses ProductReferenceCodec, ProductDescriptorCodec on the retained
descriptor_content text, DescriptorSourceCodec, UTC timestamp decoding, closed
status and RegisteredProduct's reference/document equality. The selected parsed
descriptor_document JSON column is not used by _row_to_registered, so this reader
does not compare it with retained content. The reference/content relationship is
checked on reconstruction; registration ID is not recomputed from that content.
Malformed data or driver exceptions may propagate. Canonical descriptor retention
does not establish image provenance, compatibility or safe public disclosure of
arbitrary metadata/source text.

Read depth: full 232-line owner, full 475-line registration language/service and
[383-line tests](../../../tests/test_registered_products.py.md), selected actual
Core document/reference codecs and current-schema table/constraints, with the
previously read complete UoW and timestamp codecs. This documentation adds no
security or data behavior; no tests, database inspection/mutation, registry
effects or cleanup were performed.
