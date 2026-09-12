Source: [control-plane-kit-operations/tests/test_secret_providers.py](../../../../control-plane-kit-operations/tests/test_secret_providers.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

All tests in this file inherit a real PostgreSQL setup: require
CPK_OPERATIONS_TEST_DATABASE_URL, install schema, truncate workspaces with CASCADE
and seed two workspaces. Even constructor and fail-on-access connection tests
inherit it. The package Docker harness supplies the intended disposable database;
these fixtures are not read-only operational diagnostics. Endpoint references,
credential handles and application secrets are synthetic. No provider adapter,
secret value, network route or custody backend is invoked.

Provider admission checks workspace isolation, exact replay, opaque endpoint
reference and absence of selected URL/Bearer/plaintext/ciphertext markers from
descriptor and selected SQL columns. A changed display name requires explicit
supersession and retains two lifecycle rows. Provider revocation retains ID and
actor/time while removing active selection. Reference tests cover same-workspace
provider/prefix/intent boundaries, changed-intent supersession, retained revoked
history, and rollback of a separate provider write made without commit. Those
checks do not establish handle existence or provider permission.

Two concurrent identical-registration tests are stronger than sequential replay:
one UoW inserts but holds its transaction, a worker attempts service registration,
and pg_stat_activity is polled until that connection reports a Lock wait. After
the first commit, the worker must return the same receipt with one row retained.
Provider and reference cases use one worker and bounded timeouts. The observation
is a database lock wait, not identification of every lock or a proof of all
supersession/revocation interleavings. There is no equivalent concurrent use/
revocation test in this file.

Timestamp tests use a fail-on-access connection for provider/reference registration
and revocation, proving offset-form input is rejected before any connection call.
Tokyo-session round-trips check microsecond admission/revocation times. Malformed
duplicate, replacement and revocation inputs must fail without changing existing
lifecycle/history. These are selected temporal laws; they do not exhaust every
calendar edge or exercise arbitrary malformed SQL rows. Metadata negatives reject
a URL display name, an api_token key and a Bearer string, rather than certify
all possible accepted metadata as non-sensitive.

Registration permission cases distinguish unrelated/read scopes and repeat schema
installation without losing admission. Secret-use tests independently reject
register/read/revoke/execution scopes, require SECRET_PROVIDER_USE, persist exact
provider/reference IDs and context, and preserve original requested_at through
later-time replay. authorize_resolution returns the same authorization identity
and pinned endpoint/credential references, with permits matching the requested
handle/intent. A changed run under the same correlation conflicts while retaining
the first receipt. These are local authorization and projection laws, not a
provider resolution, signed capability or transport authentication check.

Additional cases admit the secrets custody-root-key and provider-credentials-document
intents, round-trip their grants and assert one row per correlation. Direct SQL
with an unsupported use intent must hit CheckViolation. Wrong workspace/intent,
revoked reference/provider and a handle pinned to a superseded provider deny
new authorization; already stored authorized history remains queryable after
revocation. The stale cases do not inject provider IO or directly test every
same-correlation replay after lifecycle changes. Source ordering establishes
active checks before replay separately.

The 200-character run-ID test persists authorization, reads it through a fresh
UoW, projects a grant and checks SQL rejection of run/bad. Despite restart in
its name, it does not restart a process or container. Other negatives reject a
201-character activity ID and slash-containing correlation. No direct tests here
exercise secret_custody_grant_for, generated receipt admission, standalone helper
status/commit assumptions, active-page continuation or the full descriptor
read projection. Fresh services/UoWs establish database persistence, not provider
availability or runtime recovery.

Read depth: full 1,096-line owner, full
[1,307-line language/service](../src/control_plane_kit_operations/secret_providers.py.md)
and [939-line store](../src/control_plane_kit_operations/postgres/secret_provider_store.py.md),
selected actual Core provider/reference/resolution/custody and schema contracts,
plus retained run/activity, UoW and temporal reviews. The harness uses this
checkout's Core and Operations, not an independently published server image.
No tests were executed for this documentation; source/fixtures remain unchanged
and no new security or runtime behavior is introduced.
