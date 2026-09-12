Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_signing_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_signing_authority_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This private adapter reads both retained secret-use authorization chains for a
node-control signing-authority reload. It returns typed authorization, reference
and provider records under shared row locks. The
[public owner](../node_control_signing_authority.py.md) defines the private result
types and error; the adapter implements their PostgreSQL acquisition boundary.
It does not select signing keys, validate grant time, authenticate the caller,
resolve a secret, sign a token or dispatch a command.

get_for_share accepts only an exact NodeControlIntendedAttempt. It executes one
parameterized SELECT and fetchone: workload authorization ID, transit authorization
ID and attempt workspace are the three parameters. Each authorization joins its
reference by registration, workspace and secret reference, and its provider by
registration and workspace; provider registration must also equal the reference's
provider registration. Both reference and provider must be active. The workload
authorization must share the transit authorization's workspace. A missing row or
failed active/linkage join produces the fixed private unavailable error.

The query selects 84 columns, two identical 42-column family layouts. Within each
family, 16 authorization fields precede 11 reference and 15 provider fields.
_family uses offsets 0 and 42 to construct AuthorizedSecretUse,
RegisteredSecretReference and RegisteredSecretProvider. Secret references,
provider identity/endpoint, intent and status fields are reconstructed through
their typed constructors; intent/prefix arrays become tuples. Required timestamps
use the actual
[PostgreSQL timestamp decoder](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py),
which requires an aware datetime and renders canonical UTC text. Optional revoked
timestamps preserve None. Metadata columns are not selected or returned.

The selected
[schema constraints](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
give authorization IDs and reference/provider registration IDs primary keys.
Those identities bound the joined result to at most one row; fetchone alone would
not prove query cardinality. There is no LIMIT, pagination or fallback search.
The 84-column positional decoder must stay synchronized with SELECT order. It
does not independently validate the tuple's total length before indexing.

FOR SHARE names all six aliases: each family's authorization, reference and
provider. Both families may designate the same physical provider row, as the
assigned fixture does. Locks remain owned by the caller's transaction. The actual
[store bundle](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
constructs this private store with the same connection as the other stores;
[PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only after a requested successful exit, otherwise rolls back, and closes
the connection. This adapter never commits, rolls back, installs schema or writes
durable facts. Autocommit use by some other caller would not preserve the intended
lock lifetime across the subsequent service checks.

In the actual reload sequence, the workspace row is already locked and the
[key selector](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
has taken shared advisory transaction locks for transit and workload purposes.
This store adds the secret-chain row locks. The public service then checks retained
actor, correlation, operation, optional provenance, family intent and key-reference
binding, reruns reference admission policy, and verifies unsigned request claims
against the clock. Those semantic checks do not occur in this SQL adapter.
The service commits before constructing its returned pair. Authority can change
after return; these are transaction-local checks, not locks covering later signing
or delivery. The advisory key protocol coordinates participating lifecycle methods,
not arbitrary SQL writers.

TypeError and ValueError from the family reconstruction become a fixed malformed
truth error raised outside the handler, without retaining the underlying exception
chain. Invalid exact attempt type and missing truth have their own fixed messages.
The public owner translates this private error to its unavailable contract.
Database execution errors, IndexError from a short synthetic row and failures
outside the selected catch set are not universally normalized. The private result
dataclasses have default field repr behavior: their contents are not a public
redacted response. They contain reference/endpoint/provenance data rather than
resolved private bytes and should remain inside the service boundary.

The [assigned PostgreSQL tests](../../../tests/test_postgres_node_control_signing_authority.py.md)
exercise actual reload transactions, selected persisted substitutions and expiry
edges, plus concurrent row updates and normal key revocations. Their paused-clock
probe establishes locks during validation, then checks successful writes after
the service returns. It does not instrument the instant of commit or pair
construction, whose ordering comes from source inspection. Literal AST/text checks
cover the one-execute/fetchone shape; an unchanged table count on repeated schema
installation is narrower than a complete schema comparison. There are no direct
synthetic malformed-row tests for this decoder in that file.

Read depth: full 246-line store and 887-line assigned PostgreSQL test, with the
previously reviewed full public owner/contract fixture. Actual UoW and timestamp
decoder were read fully; selected bundle wiring, key selection/revocation/advisory
locking and schema table/primary-key declarations were inspected. No executable
validation, source/pin change, credential/key read, database/provider/runtime
action or publication occurred for these notes. Documentation adds no security
surface; operational use retains the caller-owned transaction and access boundary.
