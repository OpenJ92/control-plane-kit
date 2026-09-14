Source: [health_effect_preparation_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/health_effect_preparation_store.py).
Maintain this companion alongside its source.

`HealthEffectPreparationStore` owns only `get(identity)` and
`insert_absent(record)` on the supplied connection. Self-contained codec admission
precedes SQL; retained owner checks precede insertion. It never commits, updates,
renews, deletes, signs, resolves a secret or calls a provider. The caller can roll
back the whole insertion. PostgreSQL arbitrates concurrent inserts: the attempt
primary-key collision returns None, while other uniqueness conflicts raise the
fixed conflict category. Unexpected driver failures propagate.

Reconstruction verifies every relational witness against the canonical preimage.
Both reads and inserts reload the original intent and typed attempt, admitted
request/run and stored plan, and both explicit executable graph projections.
Existing owners validate their representations. Core graph validation and the
accepted management health projection rederive graph and relation pins. The wire
revision names the authored graph selected by the stored operation's graph side,
including when the two graphs have equal content. No current workspace or implicit
identity projection fallback participates.

The retained signing keys must match purpose, issuer, key ID and registration;
the existing pure registration-ID owner rebinds each ID to its exact public/private reference material, and private references and public fingerprints must differ across families. Each
retained authorization joins its exact reference and provider registrations and
key material. Both actors agree; operation/session/run/activity context agrees
with the original intent. Correlation and authorization are reconstructed through
the pure existing owner helpers with empty actor scopes. These are historical
identity checks, not active provider/reference/key admission or caller mapping.

Original health STEP_STARTED ownership remains necessary after the attempt
settles. Expiry, revocation, rotation and workspace advancement do not erase
historical evidence. Missing or corrupt retained owners refuse with bounded,
detached errors. There is no reverse requirement that unrelated old attempts have
health preparations, and no replay repair or second attempt state machine.

SQL CASE expressions bound preimage and copied text before transfer. The private
current validator traverses at most eight rows per structured keyset page and
uses exactly the normal reconstruction path, including missing-owner detection.
The new leaf has restrictive foreign keys; owners restore first, dependents delete
first only under a separately authorized retention plan. Tests cover restart,
caller rollback, races, independent witness drift, coherent false owner context,
historical reads and corruption past the first scan page. #1852 will insert the
original event/intent/attempt, uses and preparation in one first-start transaction;
#1846 owns later fresh authority and dispatch composition.

Review hardening adds three laws without weakening the original targets: swapping
both valid public PEM/fingerprint pairs cannot preserve their old deterministic
registration IDs; adapter TypeError/ValueError/KeyError during owner execute or
fetch must escape as the original object. A private read boundary distinguishes
those I/O failures from the existing owner's missing/decoding refusal. Generic
retained-data translation is confined to owner getter calls. Pure health joins
raise an explicit mismatch, and public graph/health/provider contract errors are
handled by their named types. No transaction, commit or lock is added by this
boundary. Native red for the original 33 laws remains unchanged; these two
review-found cases have source-level counterexamples and await native execution.

A ninth-row regression places an oversized corrupt raw seek key lexically before
a full lawful page. Seek and ordering both explicitly name the table's raw indexed
keys. CASE-bounded output aliases are only transport projections, never ordering
keys; otherwise a NULL projected key could sort last and disappear behind the next
raw-key seek predicate. Corrupt seek material is refused without unbounded transfer.

An internal adapter-failure carrier preserves provenance past named retained
contract categories too. An adapter-raised SecretProviderRegistrationError must
escape as the same object even though that class can also describe retained data.
The carrier is handled outside the retained-data translation and never exposed.
