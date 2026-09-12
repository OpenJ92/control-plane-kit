Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

EffectAttemptReconciliationService coordinates one exact durable attempt: it
returns retained direct-fold truth or obtains an observation and delegates guarded
folding. It consumes the [reconciliation language](effect_attempt_reconciliation.py.md),
Core observation/intention contracts, Operations stores and secret-use authority.
It owns orchestration and selected error translation, not provider truth, automatic
recovery, new graph advancement or atomic folding itself. The service is this
499-line file's only export.

Construction retains the injected UoW factory, observer and fold service, and
creates a SecretUseAuthorizationService using that same factory. It does not open
a transaction or validate every injected object's behavior. execute revalidates
the exact command and requires execution:operate before calling the factory.
Invalid commands receive the fixed InvalidOperationCommand message; missing scope
receives the specific reconciliation denial. These are separate from durable
current-claim checks performed inside the initial UoW.

Initial reads occur in request, request-scoped run, attempt order. Each helper
calls its corresponding locked store method and requires an exact record type.
KeyError becomes fixed NotFound; OperationsRecordError or ValueError becomes
fixed invalid-truth Conflict. These errors are raised after leaving the handlers,
without retaining the caught raw message/cause/context. Other exceptions, or later
bad nested members of a deliberately forged exact record, are not universally
caught by those wrappers.

_require_current_claim joins request ID to the command, run ID to command identity,
run admission to request, run plan to request plan and attempt identity to command.
It requires request status CLAIMED, a claim and exact command fence equality.
Command validation already binds authority worker to fence worker. It does not
choose the latest run, check every run status or independently reread all graph
and plan records. The actual scoped-run store filters both request_id and run_id
and uses FOR UPDATE; the service depends on its injected stores for lock behavior.

Historical authority is a separate check. Any prior recovery_decision rejects
as incongruent. A command generation below the attempt's historical generation
also rejects, as does an equal generation with another worker. A newer generation
is permitted only after it has matched the current claim. The attempt's historical
fence is not rewritten. These checks precede the choice of replay versus fresh
observation, so stale current authority cannot use retained state to bypass them.

Any admitted state other than STARTED takes _existing_fold; this includes direct
UNCERTAIN, not just successful terminal state. The branch fetches an outcome by
attempt identity and latest event ID, requires exact aggregate type, request
workspace agreement and exact attempt equality, then constructs ExistingFold.
Missing/corrupt aggregate or constructor rejection becomes fixed invalid-truth
Conflict. It does not manufacture a result or observe again when retained evidence
is absent. A recovered attempt was already excluded by the historical check.

ExistingFold reconstruction validates attempt/outcome consistency through the
fold language. There is no caller-supplied outcome or idempotency key in this
command: this branch returns the selected durable direct fold, not a newly supplied
observation. It skips this service's fresh lease, explicit intent and active-authority
reads, secret authorization, observer and fold invocation. That is not zero DB
work: it locks the initial records and reads the aggregate. The actual outcome
store can additionally reread intent for VerificationCompleted membership checks.

Neither initial branch requests commit. With the actual
[Postgres UoW](postgres/unit_of_work.py.md), returning from replay or leaving the
fresh-read context without a commit request rolls back that read transaction and
closes the connection. It does not prove the existence of a physical commit or
durable mutation. Exit/rollback/close failures can prevent the return. Arbitrary
injected UoWs remain responsible for equivalent transaction semantics.

For STARTED, the service observes the lease first. KeyError/OperationsRecordError
from that call mark invalid truth. It requires an exact ExecutionRequestRecord in
the observation, a string observed_at, a bool expired and equality with the initial
request. It checks those fields, not an exact outer observation class or a complete
timestamp parse here. Invalid observation conflicts; valid expired observation
denies before intent or active-authority lookup.

The stored intent must be an exact EffectAttemptIntentRecord with matching attempt
identity, original event, request ID, workspace and request fingerprint. Missing
or record-validation errors conflict. If the intent has an authority reference,
the service performs a workspace/reference active lookup. NotFound denies;
registration errors or wrong outer type conflict. A returned exact registration
with different workspace/reference/runtime kind or non-active status denies.
No-reference intent leaves runtime_authority=None. This checks stored coordinates;
it neither resolves credentials nor establishes provider availability.

After those reads pass, the initial UoW exits before _fresh_observed_fold. The
service deliberately does not hold those locks throughout secret authorization
and provider observation. The recorded observation time is carried forward rather
than replaced by a fresh clock before each use. Changes can occur in this interval;
later guarded-fold revalidation protects its own mutation boundary, not retroactive
authority over an observation already performed.

The fresh helper constructs the runtime request twice in a fixed two-pass loop:
first without grants to enumerate/authorize uses, then with the resulting grants.
This is not retry or a second observer call. It binds the original start-event ID
to the persisted intent both times. required_secret_uses_for_runtime_effect gathers
product secret deliveries, pull credentials, authenticated PostgreSQL check
passwords and remote Docker TLS references, deduplicating/sorting reference-intent
pairs. Non-tuple enumeration or nonempty uses without secret-provider:use denies.
Empty uses require no secret-use authorization calls.

Each use becomes AuthorizeSecretUse with intent workspace/request, current
run/activity/original event, worker subject/scopes and the initial observed time.
The correlation includes workspace, reference, use intent, actor and operation/
run/activity/effect coordinates; it does not include request time, scopes or fence
generation. No raw secret value is constructed here. Authorization-denied,
registration-error or use-conflict exceptions and non-exact grant returns become
the fixed authority denial; unexpected failures remain unnormalized.

The actual [secret-use service](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
locks the correlation and current reference/provider admissions, checks permitted
intent and compares durable fingerprint on replay. authorize_resolution creates
the routing/reference grant and requests commit in its own UoW. Every use has a
separate transaction: earlier authorizations can remain committed if a later use,
request construction, observer or fold fails. This orchestrator performs no batch
rollback or automatic revocation. A grant identifies authorized use and routing
references, not a resolved secret or proof of successful provider access.

Remote TLS authority supplies an explicit connection-admission value containing
the same authority reference and three TLS reference roles. Core's observation
request validates exact grant types, duplicate uses, request-coordinate matching
and admitted use domains, including connection grants. That validates the supplied
grants, not a generic completeness proof for every conceivable provider's needs;
the enumeration and provider contracts remain meaningful dependencies.

The observer is invoked once with the request and selected registration, outside
the initial read and completed authorization contexts. There is no try/except
around that call, no timeout/retry loop and no general raw-exception redaction.
BaseException and unexpected observer exceptions propagate. The observer's declared
read-only behavior must be upheld by its implementation. This service cannot
prevent a malicious injected observer from mutating resources or opening its own
transaction, and it does not store a separate observation-attempt receipt here.

Returned material is wrapped as ObservedEffectOutcome. Record errors, wrong
outcome type or effect-ID/request-fingerprint mismatch become invalid truth. It
derives the transition/failure and constructs FoldEffectAttempt plus
GuardedObservedEffectFold from the original command, persisted intent and selected
authority. Selected command/record/ValueError failures normalize; raw bodies or
provider messages are not copied into the fixed conflict message. Safe outcome
admission and canonical failure projection remain owned by those value contracts.

The injected fold service receives execute_observed. FoldDenied maps to authority
denial; FoldConflict or FoldNotFound maps to incongruent conflict. These translations
are raised after handlers. A normal return must be exactly NewlyFolded or
ExistingFold; it is returned unchanged, without reconstructing or independently
joining its nested fields again here. Thus the injected fold is a trusted semantic
boundary: exact result type alone is not deep validation of an object forged
without its constructor. Other fold exceptions are not caught.

The actual [guarded fold interpreter](effect_attempt_fold_interpreter.py.md)
relocks and checks current request/run/attempt, handles exact replay, or validates
fresh stored intent, lease and active registration against the supplied guard,
then atomically writes event, endpoint observations, outcome and attempt CAS.
It can reject drift that arose after the initial reconciliation reads; if another
fold already won, its replay rules govern. It allocates IDs and commits on its
own terms. This service supplies neither an ID factory nor a distributed lock
spanning observer and fold. Concurrent reconciliations can both reach observation;
no durable exactly-once observer claim or reversal of earlier reads is made.

The reviewed [language tests](../../tests/test_effect_attempt_reconciliation_contract.py.md)
and [interpreter contract](../../tests/test_effect_attempt_reconciliation_interpreter_contract.py.md)
cover selected value/publication/preflight/lexical boundaries, not the complete
transaction narrative above. The
[PostgreSQL fixture](../../tests/postgres_effect_attempt_reconciliation_fixture.py.md)
separates ledger contexts from actual transaction state and documents shared
oracles and committed setup/authorization. Existing direct-fold aggregate rules
and UoW behavior inform replay; stronger consumer tests must retain their own
review dispositions rather than being credited merely because they import this
service. No successful live observation, recovery or cleanup follows from this note.

Read depth: all 499 owner lines, every branch/helper/import; retained full language,
fold language/interpreter and UoW; refreshed scoped SQL, outcome replay/membership,
required-use enumeration, correlation, per-use admission/commit/grant and Core
request/connection-grant validation; reviewed contract675/584 and fixture385
context. Selected store/secret/Core paths are not whole-package audits. No imports,
tests, databases, credentials, providers, Docker or source/dependency edits ran
while authoring this companion.
