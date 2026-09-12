Source: [control-plane-kit-operations/tests/postgres_effect_attempt_reconciliation_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_reconciliation_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 385-line fixture supplies PostgreSQL reconciliation setup, observer/fold
doubles, transaction-context accounting and expected command/snapshot builders.
It inherits
[PostgresGuardedObservedEffectFoldFixture](postgres_guarded_observed_effect_fold_fixture.py.md),
including real database setup and persistence helpers. It defines no test methods
and does not establish a passing reconciliation law by itself. Its four fixed
error strings are comparison vocabulary, not exception handling or redaction.

UnitOfWorkLedger wraps a supplied factory. Calling it creates the underlying unit
of work immediately; entering first delegates to that object's __enter__, then
increments active and entries and returns the actual entered object. A failed
underlying entry is not counted. Exit delegates to the underlying __exit__ and
updates active/exits in finally, including when exit raises. The delegate's return
value and exception behavior are preserved. The wrapper does not implement its
own commit, rollback or retry policy.

The ledger counts only contexts opened through its factory. active stays nonzero
through the delegate's commit/rollback/close, but active == 0 and entries == exits
do not prove commit success, PostgreSQL transaction state, connection closure or
absence of other transactions. Setup and secret registration use the inherited
unwrapped factory. A caller-supplied fold service can also use another factory.
The counters have no concurrency isolation and are not a general transaction
monitor.

RecordingObserver optionally rejects a call while its supplied ledger is active,
before recording that call. Otherwise it appends (request, authority), then raises
the supplied error object or returns the stored result unchanged. The caller must
give the observer the same ledger used by the service; the service factory does
not attach it automatically. This double neither validates observations nor
inspects a provider. FailIfObserver and FailIfFold append their input and raise a
stored AssertionError, making prohibited invocations visible to consumer tests.
These doubles retain their supplied objects without a separate redaction layer.

reconciliation_command uses the supplied current attempt or reads current_attempt,
then constructs the actual ReconcileEffectAttempt with fixed request-a, that
attempt's identity, and default worker-a/generation seven authority and fence.
The default scope is EXECUTION_OPERATE only. Required secret uses need a caller
to supply SECRET_PROVIDER_USE as well. The request ID is not derived from a
replacement attempt, and constructing the command does not authenticate a claim.

observation_for replaces a story result's effect ID with the original start-event
ID and its request fingerprint with the production fingerprint of the supplied
intent. Other result fields survive, subject to the actual result constructor's
admission. observer_for puts that synthetic result in RecordingObserver. Neither
helper discovers runtime state or independently computes an expected fingerprint.

reconciliation_service constructs the actual
[reconciliation interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
using the ledger when supplied, otherwise self.unit_of_work. Without an explicit
fold service it also constructs EffectAttemptFoldService with that factory and a
Sequence of supplied IDs or story-derived IDs. Empty IDs select the defaults.
A supplied fold service is kept as-is, and IDs are then unused. The selected story
controls default ID generation; it does not change the observer's stored result.

The actual interpreter creates its secret-use authorizer with the same factory.
Its fresh path reads and validates durable attempt/lease/intent/authority truth
inside an initial context, authorizes required uses in separate contexts, calls
the observer outside those contexts, and delegates the guarded fold. Default
wiring lets the ledger count these contexts, including the fold. In the selected
[observer/fold consumer](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_observer_fold.py),
the explicitly supplied fold service uses the unwrapped factory: the asserted
1 + len(uses) entries count the initial read and authorizations, not the fold.
This distinction prevents interpreting that assertion as an all-transaction count.

seed_reconciliation_source normally delegates to seed_guarded_source, then
registers local or remote runtime authority when the resulting intent has an
authority reference. It returns current attempt, intent, intent record and
authority. These helpers reset and write real test history and registration rows
across setup transactions; they are not one atomic seed operation. Inherited
PostgreSQL setup/teardown includes table cleanup, so this fixture belongs in an
isolated test database. Seeding does not admit secret references or issue grants.

With zero_use, the helper resets normal or compensation start truth, builds a
STARTED run-a/start-runtime attempt, removes the authority reference and inherited
authority deliveries, and replaces products with an empty tuple. It rebuilds the
state fingerprint and original event evidence before persisting the attempt and
intent, then returns None for authority. This branch takes precedence over remote,
authority_ref and process_delivery options. It provides a representable empty-use
request, not evidence that a real provider can execute an empty-product start.

expected_observed_fold constructs an ObservedEffectOutcome from observation_for,
uses production transition/failure derivation and the intent's request ID, then
builds the inherited guarded command with register=False. It does not execute a
fold or perform a new authority registration. Its worker/fence remain the default
worker-a/generation seven rather than following a separately customized
reconciliation command. Because it shares production outcome derivation, equality
with this expected command is not an independent oracle for those algorithms.

runtime_request calls the actual request factory with the original start-event ID
and supplied grants. required_secret_uses calls the production enumerator on a
request without grants. The inspected enumerator collects product secret
deliveries, OCI pull credentials, authenticated PostgreSQL verification passwords
and remote Docker TLS authority references, deduplicating and sorting pairs of
reference and intent. Enumeration performs no authorization or secret resolution.

admit_secret_uses builds sorted unique intents and parent reference prefixes, then
uses the actual
[secret-provider registration service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
to register a workspace-a/local CPK Secrets provider and one reference admission
per input pair, each allowing that pair's single intent. The endpoint and bootstrap
credential are reference values; the helper does not resolve them or contact a
secrets server. It supplies SECRET_PROVIDER_REGISTER and fixed synthetic operator,
time and metadata values.

Provider registration and every reference registration each have their own unit
of work and commit. A later rejection can leave earlier admissions committed;
the helper offers no batch rollback or compensation. Actual admission checks
workspace/provider identity, allowed intents and reference-prefix containment.
The helper assumes reiterable local fixture uses, does not aggregate multiple
intents for one reference, and promises no repeated-admission idempotency. Empty
uses are not a no-op: provider admission requires nonempty allowed intents.
Registration uses self.unit_of_work directly, outside an independently created
ledger, and does not itself authorize any particular effect or produce a grant.

authorization_command builds AuthorizeSecretUse from the intent workspace/request,
current run/activity/original effect, requested reference/intent and reconciliation
worker/scopes. It uses production secret_use_correlation_for; that correlation
includes the use and operation coordinates but excludes requested time, scopes
and lease generation. Session/probe values remain their defaults. This helper
constructs a command only. The actual authorization service checks SECRET_PROVIDER_USE,
validates canonical UTC time, locks admission/correlation truth and commits each
authorized use before returning a resolution grant. Earlier uses can therefore
remain committed if a subsequent authorization or observer call fails.

authorization_rows selects twelve columns from all secret-use authorization rows,
ordered by actor, reference and intent. It omits provider/reference registration
IDs, intent fingerprint and probe ID, and does not snapshot admission tables or
resolve secret values. complete_reconciliation_snapshot pairs those rows with the
inherited selected attempt/non-advancement snapshot. Despite the name, it is not a
complete or atomic database snapshot; equality cannot prove unchanged omitted
fields, runtime-authority registrations or secret-provider admissions.

lease_observation patches the store method with a wrapper that first executes the
actual locked lease observation, then replaces only observed_at and expired in
its returned value. It preserves the returned request and does not update durable
lease expiry or replace the database clock globally. observation_request directly
constructs the actual
[RuntimeEffectObservationRequest](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
without connection admission. The owner validates grant types, duplicates and
request coordinates; connection grants also require connection-admission
validation. This convenience helper does not build the remote TLS admission that
the actual reconciliation interpreter supplies, nor does request construction
prove every required use has a grant.

Read depth: the complete 385-line source and inherited guarded-fold fixture were
read, alongside selected actual reconciliation, fold, unit-of-work, observation,
required-use and secret-registration/authorization contracts. Selected observer/
fold and authority/grant consumer sections were checked for ledger wiring,
zero-use setup and committed authorization evidence; this is not a full review of
those consumer suites. Validation for this companion is documentation-only:
relative links, whitespace and source-base comparison. No application imports,
test execution, database/provider calls or credential access were performed.
