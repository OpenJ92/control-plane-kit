Source: [control-plane-kit-operations/tests/test_registered_products.py](../../../../control-plane-kit-operations/tests/test_registered_products.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This suite covers the [registration language and services](../src/control_plane_kit_operations/products.py.md),
product persistence and image-pull authority reference persistence. The source
codec class is pure; the store class requires
CPK_OPERATIONS_TEST_DATABASE_URL, installs the schema, truncates workspaces with
CASCADE and seeds workspace-a before each test. The Operations Docker harness
provides the intended disposable database. The store cases are destructive test
fixtures, not read-only diagnostics suitable for a retained operational database.

The pure test round-trips all three acquisition evidence variants: inline,
remote descriptor URL with expected hash, and catalogue URL with identity and
expected catalogue hash. It rejects an unsupported local-path kind, URL userinfo
and a query containing a credential marker. Despite the test's secret_free name,
these assertions cover those selected shapes; they do not prove arbitrary URL
paths/metadata contain no secrets, exercise every codec field rejection, fetch
any source or validate its claimed expected digest.

Product fixtures are generated through Core's ProductDescriptorCodec using
synthetic OCI digests, a display tag and one declared HTTP provider socket.
Import must retain workspace, product identity, descriptor-content digest,
microsecond admission time and active status, with one row visible through a
fresh UoW. Duplicate content imported with a different source, actor and time
returns the entire first record and leaves one row. A different image digest
changes descriptor content while preserving ProductIdentity; sequential admission
must raise the replacement conflict and retain one row. This establishes local
descriptor admission policy, not registry existence or image/contract equivalence.

The invalid product timestamp case expects the fixed canonical-UTC error and
zero rows. The source places encoding before lookup, but this test uses a real
connection and checks the resulting row count rather than instrumenting every
database access. A direct store write without UoW commit must roll back. Revocation
removes the product from list_active while get still returns its revoked receipt;
the name not_selectable describes active selection rather than total erasure.
Re-import after revocation, concurrent conflicting imports, manually mismatched
document values/bytes and tampered redundant descriptor JSON are not covered.

Image-pull cases similarly check workspace admission, microsecond time, active
selection, an empty other-workspace list, exact replay, and sequential conflict
when the same registry/repository scope has a different credential reference.
Revocation retains a gettable revoked record and removes it from active selection.
Invalid timestamp and no-commit rollback cases mirror the product behavior.
The companion
[image-pull authority store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/image_pull_authority_store.py)
owns those persistence decisions; this file does not resolve its synthetic
secret:// reference, validate provider custody, call permits against an image,
authenticate to a registry or exercise overlapping repository-scope selection.

Fresh UoWs demonstrate transaction visibility and rollback, not an actual server
process restart. The cases are sequential, with no concurrency or crash/failure
injection around admission. Neither service accepts an authenticated principal
or checks caller scopes itself, and these tests do not establish transport-level
authorization. Retained attribution/status are exercised; a separate history of
revocation events is not asserted. No live image, deployment or provider evidence
should be inferred from these assertions.

Read depth: full 383-line test owner, full 475-line language/service, full
[232-line product store](../src/control_plane_kit_operations/postgres/product_store.py.md)
and full 186-line authority store; selected same-checkout Core product/document/
pull-authority definitions, current schema and harness dependency wiring, plus
previously read UoW/temporal contracts. The harness installs this checkout's Core
and Operations; it is not a published-server or independent SDK-pin test. No
tests were run for this documentation change, which adds no security or runtime
surface and leaves all test fixtures unchanged.
