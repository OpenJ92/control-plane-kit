Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_authority_grants.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_authority_grants.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven PostgreSQL tests cover runtime-authority selection, secret-use
admission/authorization, durable authorization replay and selected error boundaries.
The [reconciliation fixture](postgres_effect_attempt_reconciliation_fixture.py.md)
owns real test setup and synthetic observer/fold doubles. This suite does not
perform a real provider observation or successful atomic fold: positive paths
stop at an injected observer error or the fold sentinel. Authoring this note did
not execute tests or open a database.

The direct control seeds a remote-authority world, requires a nonempty sorted
deduplicated use tuple, admits those uses and calls the real
SecretUseAuthorizationService.authorize_resolution for each. Repeating with a
later requested_at must return equal grant tuples and leave exactly len(uses)
authorization rows. The helpers and service share production enumeration and
correlation logic. This proves selected authorization replay across time changes,
not independent enumeration completeness for all runtime operations.

The secret-value-canary exclusion checks repr of those returned grants, but that
canary was never provided as a secret value to the service. The test neither
resolves a credential nor injects hostile provider output. It does not inspect
every grant field, check every descriptor or prove universal grant redaction.
Actual grants retain routing and authorization references rather than secret bytes.

The no-reference test contains three separately seeded worlds. Its zero-use world
removes products and authority reference, requires no enumerated uses, forbids
active-authority lookup and authorize_resolution, and allows a synthetic observer
to reach the stored fold sentinel. It requires that exact fold error object,
one observer call with None authority and an empty authorization table. Three
wrappers record request/run/attempt method entry before delegating to real stores;
the exact sequence request, run, attempt, request exposes the lease observer's
second request read. This is selected method-entry order, not SQL/PID lock proof
or exclusion of all other store reads.

The secret-bearing execution-scope-only world must deny with the fixed authority
message, no cause/context, no authorization rows and empty observer/fold call lists.
The no-reference-product world retains nonempty product uses, admits them and
supplies secret-provider:use. It still forbids runtime-authority lookup, but must
authorize len(uses), invoke the observer once with None and reach the exact fold
sentinel. Thus no authority reference does not imply no secret uses, and the
zero-use fixture is not representative of every local runtime request.

Two lawful referenced-authority rows seed local and remote registrations but patch
get_active_for_update to return the selected registration while recording its key.
Each expects one workspace/reference lookup and any AssertionError from the
invocation. Unlike the earlier exact-sentinel worlds, these rows do not assert
observer-error identity or observer/fold call lists, so that broad exception check
alone is not a precise observer-arrival witness. Excluding registration_id from
repr(command) is unsurprising because that command carries no registration object.

Four selector faults inject NotFound, a lawful REVOKED registration, a foreign
workspace or a foreign reference. They must yield the fixed authority denial with
no cause/context. These are patched returns/exceptions, not SQL revocation or
foreign-row mutation. The method title includes runtime-kind exactness, but this
matrix does not supply a wrong-kind registration. Active-selector patches use
create=True and therefore do not themselves prove the method's existence. Real
registration/setup occurs before these patches through the fixture.

A further three selector-exception cases map RuntimeAuthorityRegistrationError
to fixed invalid-truth Conflict with no cause/context, while TypeError and
RuntimeError must escape as their original objects. The sentinels would fail an
unexpected downstream call, but these methods do not separately count IDs, take
complete snapshots, inspect grant rows or audit all lower interactions. They prove
the selected exception policy, not blanket sanitization of arbitrary failures.

The actual [reconciliation interpreter](../src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py.md)
checks current claim and lineage, lease and stored intent before required active
authority. It joins workspace/reference/kind/status, then leaves its initial UoW
before per-use authorization and observation. Those source checks explain the
tests; the fixture's mocked selector and lease values must not be described as
independent proof of the corresponding SQL predicates.

The grant-retry test exercises real authorization through a recording wrapper.
A remote world admits uses, attaches the same UnitOfWorkLedger to service and
RecordingObserver, and injects a stored observer RuntimeError. The first call
uses a patched lease-result time and must escape with that identical error.
Recorded authorization commands must equal the full expected command tuple;
selected SQL row projections must match their command projections. The ledger
requires active=0, entries=exits=1+len(uses): one initial read context plus each
authorization, with no fold reached because the observer raises first.

The later explicit second invocation records commands with the new time, reaches
a different stored observer error, preserves the entire selected authorization-row
tuple and doubles ledger entries/exits. RecordingObserver itself rejects active
ledger contexts, so the identical error is evidence that it ran outside those
tracked contexts. This is caller-driven repetition, not an automatic retry loop.
The ledger counts wrapped contexts, not all connection activity or physical commit
success; registrations/setup are outside it. No assertion compares every field
of the observation request's grant tuple in these two invocations.

The partial-authorization test requires at least two uses and makes the second
authorize_resolution call raise before its real body, while the first delegates
and commits. Fixed denial must leave one authorization row. A second explicit
invocation with the same worker and later time reaches its injected observer error,
leaves len(uses) rows and preserves the first committed row exactly. Projection
checks require its original time and later times for the newly completed uses.
This is direct evidence that earlier authorization survives later failure; there
is no batch rollback or compensation claim.

The same test then updates the current claim through fixture setup to worker-b,
generation eight, and invokes with matching coordinates and another patched time.
It requires two actor groups totaling 2*len(uses), unchanged worker-a rows and
worker-b row projections matching newly derived commands. The expected-command
scope checks concern helper-produced values; this phase does not record actual
authorization command objects as the prior grant-retry test does. Changing actor
and generation together does not isolate generation's effect on correlation, nor
exercise a public claim-transfer workflow.

Both projection helpers compare eleven fields: workspace, reference, intent,
subject, correlation, normalized UTC time, operation, session, run, activity and
effect. The row helper drops authorization_id and converts its datetime using
isoformat. The fixture query returns twelve columns but omits provider/reference
registration IDs, intent fingerprint and probe ID. Exact row equality is stronger
for those twelve selected columns than projection equality, but neither is a full
authorization/admission/database snapshot. Expected correlations share the actual
production helper rather than an independent hash implementation.

The real [secret-use owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
locks correlation and active reference/provider admissions, checks the use's
allowed intent and compares the current candidate fingerprint with any retained
authorization. authorize_resolution commits one use before returning its grant.
Correlation includes actor and operation/run/activity/effect/use coordinates,
not requested time, scopes or fence generation. This explains equal grants with
later requested times and new actor rows. Earlier admissions and authorizations
remain separate transactions from any later observation/fold.

The final method reuses one seed and patches enumeration to a one-use tuple. Three
injected expected authorization exceptions must map to the fixed denial with no
cause/context; two raw TypeError/RuntimeError objects must propagate identically.
It does not invoke real authorization under those patches, check partial commit,
test malformed grant returns or count observer/fold calls explicitly. Expected
error categories must not be promoted into coverage of every real admission denial.

The [use enumerator](../src/control_plane_kit_operations/runtime_effects.py.md)
and fixture remain shared inputs. Lease-time helpers call the real observer and
then replace only returned time/expired fields; they do not rewrite durable expiry
or prove the database clock boundary. This file adds no concurrency, provider
resolution, cleanup, ID-allocation or global redaction evidence. Its synthetic
errors and registrations are bounded test inputs, not current external authority.

Read depth: all 629 source lines, seven tests, both projection helpers and every
nested wrapper; retained full reconciliation499/fixture385/fold/UoW context;
refreshed actual enumeration, correlation, per-use admission/commit/grant paths,
fixture sentinels/ledger/row query and lease wrapper. Selected dependency paths
are not whole-package audits. No imports, tests, databases, providers, credentials,
Docker or source/dependency changes were executed while authoring this companion.
