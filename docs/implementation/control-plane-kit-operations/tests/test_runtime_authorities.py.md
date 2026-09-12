Source: [control-plane-kit-operations/tests/test_runtime_authorities.py](../../../../control-plane-kit-operations/tests/test_runtime_authorities.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This suite covers runtime-authority values and real PostgreSQL admission for
authorities and delivery values. The value class is pure. The store class
requires CPK_OPERATIONS_TEST_DATABASE_URL, installs schema, truncates workspaces
with CASCADE and seeds two workspaces before each test. It must use the package
harness's disposable test database, not retained operational data. No test creates
a Docker connection, mounts a socket or resolves the synthetic secret references.

Value tests distinguish public endpoint redaction from full storage-codec
round-trip: the public descriptor retains reference handles but hides the host,
while storage preserves it. Negative cases reject HTTPS where tcp is required,
userinfo and an unknown private_key field. The names secret_free and
secret_shaped describe these selected assertions, not comprehensive arbitrary
content scanning or TLS validation. The unknown-key case checks error category,
not every possible exception payload's redaction.

Authority admission checks workspace, reference, Docker kind, remote-authority
kind, microsecond timestamp and active selection with an empty other-workspace
list. Sequential identical registration replays; a changed endpoint under the
same active reference conflicts. Revocation removes active selection while get
retains the same registration ID with revoked status. A direct store write
without commit disappears in the next UoW. A malformed timestamp yields the
fixed canonical-UTC error and zero rows; it does not instrument the connection
to prove the absence of every preceding lookup. The
[store source](../src/control_plane_kit_operations/postgres/runtime_authority_store.py.md)
separately shows timestamp encoding before its own SQL.

Delivery cases require a registered active authority, covering both missing and
already-revoked authority denial. Local delivery checks active status, microsecond
time and absence of a socket path from the descriptor. TLS delivery registration
replays; replacing it with cloud-credential delivery under the occupied reference
conflicts. That conflict does not prove variant compatibility is validated when
the delivery slot is empty. The TLS delivery references intentionally differ from
the remote authority's connection references; this is admission of separate
values, not proof those credentials match. No-commit rollback, malformed-time
rejection and independently retained delivery revocation are also exercised.

Read-model cases use InstanceReadService with the real store bundle and one
active page. Authority output must hide host/port; delivery output replaces
secret_references with a redaction marker and excludes selected secret-handle,
socket-path, PEM and host strings. The additional transformation belongs to the
[authority read projection](../src/control_plane_kit_operations/read_services/authority_secrets.py.md),
not the raw delivery descriptor or storage codec. These cases do not traverse
multiple pages, mutate redundant columns or prove all metadata is safe.

The focused-scope test denies each of the four commands with an unrelated or
neighboring scope and exercises successful authority/delivery registration.
Caller-supplied PolicyScope values are the fixture authority input; no HTTP/MCP
authentication middleware, principal derivation or provider permission is tested.
Fresh UoWs show persistence/rollback, not process restart or credential delivery
to a recipient. Concurrent registration/revocation, exact re-registration after
revocation, get_active_for_update error/locking behavior and the private snapshot
delivery helper are outside this file's assertions.

Read depth: full 701-line test owner, full 674-line
[language/service](../src/control_plane_kit_operations/runtime_authorities.py.md),
full 595-line combined store, selected actual Core reference/delivery contracts,
schema and projection/redaction entry points, with previously read UoW/temporal
contracts. The harness uses same-checkout Core/Operations source. No tests or
database operations were run for these notes; source and fixtures remain
unchanged and no new security/runtime surface is introduced.
