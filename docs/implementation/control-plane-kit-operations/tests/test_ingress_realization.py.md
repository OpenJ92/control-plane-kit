Source: [control-plane-kit-operations/tests/test_ingress_realization.py](../../../../control-plane-kit-operations/tests/test_ingress_realization.py).
Maintain this document alongside its source file. When the test or relevant ingress
realization contracts change, verify and update this companion in the same change.

This 1,158-line unittest suite contains 13 tests for allocation, generated-reference
recording, compensation and removal through IngressRealizationAdapter. It combines
actual PostgreSQL stores with recording provider and secret-authorizer doubles.
The tests distinguish selected durable outcomes around provider calls; they do not
exercise Cloudflare, DNS, tunnels, secret-byte custody, a coordinator execution loop
or live restart/history recovery. This companion records source inspection, not a
new test run.

Every test requires CPK_OPERATIONS_TEST_DATABASE_URL. setUp opens an autocommit
psycopg connection, installs the schema, truncates cpk_workspaces CASCADE and inserts
workspace-a directly. It then commits registration of a generated-secret provider
and a Cloudflare ingress authority through the tracking unit of work. The provider
admits the generated/ingress reference prefix and tunnel-token intent; the authority
admits a fixed hostname pattern and carries synthetic account/zone IDs and secret
references. These are fixture values, not credentials retrieved from a live service.
tearDown closes the setup connection; it does not truncate again. The destructive
setup belongs to the isolated Operations test apparatus. No imports, setup or SQL
were executed for this documentation change.

TrackingUnitOfWorkFactory opens fresh psycopg connections through the actual
[Postgres unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
Its wrapper increments entered and active before entering the inner unit of work,
counts commit() requests before forwarding them, and decrements active in finally
after inner exit. The inner unit of work physically commits on successful exit when
requested. Thus committed is a request counter, not a physical-commit receipt.
An inner-enter failure can leave the wrapper's active counter incremented; that
fault is not injected here. Zero counts observed by recording providers show that
no unit of work tracked by this factory is active at those call boundaries.

RecordingIngressInterpreter records create names, origins, authorities and both
grants, plus teardown resources/grants and active counts. create runs an optional
on_create callback, then raises an optional supplied exception or returns a frozen
FakeIngressAllocation. The normal receipt copies custody ID, provider registration
and reference from the supplied grant and supplies a fixed version ID/number.
Flags instead return a different receipt reference or a newline-bearing tunnel ID.
teardown records its arguments and optionally raises RuntimeError; it does not
remove a provider resource. These doubles do not implement provider reconciliation,
network mutation, token delivery or remote cleanup verification.

RecordingSecretUseAuthorizer records AuthorizeSecretUse commands and returns a real
SecretResolutionGrant value with copied command coordinates and fixed synthetic
authorization/provider/reference IDs and fingerprints. It does not run the durable
secret-use authorization service. The selected
[Core secret contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
validate those values; grant.permits checks reference and intent, while
receipt.matches checks custody ID, provider registration and reference. Matching
these values does not independently verify possession of secret bytes, receipt
status/version against the grant, or all authorization context fields.

The graph fixture builds gateway and cloudflared nodes under a Docker runtime, with
an HTTP provider endpoint at http://gateway:8000 and a named public ingress using
the default EPHEMERAL lifecycle. These are topology values; no container is started.
context() constructs an in-memory CLAIMED execution request, RUNNING run, plan,
base/desired identity projections, EXECUTION_OPERATE and SECRET_PROVIDER_USE worker
scopes, lease fence and STEP_STARTED intent event. The request, approval IDs, plan,
run and event are not persisted by this helper. Supplying these values does not test
authentication, approval admission, lease acquisition or coordinator journal writes.

record_existing_ingress registers a synthetic secret reference and records an owned
resource plus generated-secret metadata in the caller's unit of work. Its caller
commits. The removal tests therefore begin with real durable ingress/reference
rows but no corresponding live tunnel, DNS record or stored secret version. The
selected [ingress store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/ingress_authority_store.py)
retains epochs/statuses and source coordinates; the tests query those records,
not an external observer.

The allocation success test requires create_active_counts=[0], the exact HTTP origin,
the authority's API reference, one resolution grant and one custody grant with the
expected permits predicates. It checks the authorization command's actor/effect coordinates,
SUCCEEDED and the exact generated name cpk-gateway-001-c0303ba7369e. Selected outcome
evidence fields must identify the provider, ingress/runtime and recorded connector
material. Its repr must omit secret://, eyj-cloudflare and bearer-value canaries.
The test reads the durable resource and generated evidence and compares tunnel/DNS/
hostname/lifecycle fields, generated reference, provider registration and version.
It does not independently query secret bytes or prove that every returned
allocation coordinate equals a requested coordinate.

The structural-provider positive matrix uses locally defined frozen dataclass and
StrEnum types rather than a provider implementation's nominal exception type.
It varies one field at a time over nine stages, seven categories, four mutation
certainties and four cleanup results, then adds four resource-presence shapes:
neither resource, tunnel only, DNS only and both. These are 28 subtests, not a
Cartesian product. Every case must yield UNCERTAIN with ingress.allocate-uncertain,
create_active_counts=[0] and no adapter teardown call. Exact failure details contain
the exception class, four selected vocabulary values, provider kind and resources
ordered tunnel before DNS. Even none mutation certainty or complete cleanup remains
uncertain. Two sensitive strings deliberately embedded in the exception message
must be absent from failure repr.

The hostile matrix has 15 subtests: a mapping; raw strings instead of enums; an
unknown enum for each of the four vocabulary fields; a secret-shaped tunnel ID; a
newline-bearing DNS ID; empty, integer and 129-character values for each ID; and a
frozen dataclass subclass with extra API-token/provider-body fields. Every case
must remain UNCERTAIN, drop the entire optional provider projection to just
exception_type, record create outside a tracked transaction and avoid teardown.
Failure repr excludes the exception-message and secret-reference canaries. The
secret-shaped ID also violates the identifier grammar, so that case alone does not
isolate the substring-denial rule. The matrix does not directly cover a nonfrozen
dataclass, a dataclass class object, attribute-access exceptions, every forbidden
substring or acceptance of a 128-character identifier.

The mismatched-custody test requires FAILED, exactly one teardown and equal create/
teardown custody-grant lists. Subsequent queries find no owned resources and no
active registered secret references. It does not query the generated-evidence table
or verify external cleanup. The projection-failure test returns an invalid tunnel
ID and requires FAILED plus one teardown whose raw allocation still contains the
newline ID and expected DNS ID, with teardown_active_counts=[0]. This specifically
protects compensation from the allocation object even when constructing the durable
resource fails; it does not make that malformed coordinate a safe provider input.

Two clock tests reject allocation before create. One supplies invalid newline text,
requires UNSUPPORTED and empty create/teardown call lists. The other supplies a
valid-looking offset timestamp rather than canonical UTC, requires UNSUPPORTED
with ingress.allocate-unsupported and empty create-count/name lists. They establish
the allocation timestamp's position before provider mutation, not every malformed
clock return or every timestamp boundary accepted by the
[canonical validator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/_temporal.py).

The test named durable_fold_race uses on_create to revoke the generated-secret
provider in a committed database transaction before create returns. The adapter
then fails its recording path, compensates exactly one allocation with tunnel-001,
and leaves no owned resource. This is a deliberate sequential interleaving through
a callback, not two concurrent workers or an injected database commit failure.
It does not exercise provider endpoint/credential replacement, every changed
admission field or ambiguity after a commit may already have succeeded.

The compensation-failure test combines a mismatched receipt with a teardown that
raises. It requires UNCERTAIN, ingress.compensation-uncertain, selected tunnel/DNS/
hostname coordinates and the exact fold/compensation exception class names. The
source also emits tunnel name, but this test does not assert that field or exact
whole-descriptor equality. It does not test malformed compensation evidence or
whether a provider actually left resources behind.

The distinct-run test allocates once, directly marks the durable resource REMOVED
through its store, then allocates again with another run and event ID. Both outcomes
must succeed and produce different names matching the cpk-gateway-001 prefix and
12 hexadecimal suffix pattern. The first removal does not call provider teardown.
This proves the selected distinct-run naming example after a removed epoch; it is
not a same-effect retry, concurrent uniqueness or general hash-collision test.

Successful removal starts from record_existing_ingress and supplies the ingress
graph as base with an empty desired graph. The test requires SUCCEEDED, teardown
outside a tracked transaction, one API grant permitting the expected reference/
intent and a passed resource whose status is REMOVING. It then queries history and
requires REMOVED with the expected timestamp and removing run. It does not assert
the complete teardown plan, actual DNS-before-tunnel deletion, secret revocation or
an independently persisted activity event sequence.

When teardown raises, a forbidden clock records calls and would fail if invoked.
The test requires UNCERTAIN, zero clock calls, teardown_active_counts=[0] and a
stored UNCERTAIN resource. The assertion proves the removal clock follows a
successful provider return, rather than running on the provider-failure path. It
does not inject a failure while persisting the uncertain status.

When teardown returns normally but the clock returns not-a-timestamp, the test
requires UNCERTAIN with ingress.remove-uncertain. The clock records the number of
unit-of-work entries, and that snapshot must equal the count after execute before
the verification query. This witnesses no additional tracked unit of work after
the bad clock. The durable resource remains REMOVING with removed_at and
removed_by_run_id absent. Failure repr must omit the bad timestamp, fixture
hostname, tunnel/DNS IDs and secret://. This protects an explicit uncertainty
boundary; it does not prove recovery or justify rerunning teardown.

The complete [adapter source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/ingress_realization.py)
explains further limits not covered directly here. Retained/external skip is checked
only after interpreter/grant/generated-evidence preflight. Allocation recording
exceptions trigger compensation without a distinct ambiguous-commit branch;
success evidence is built after that handler. Removal status writes and final
evidence construction are not all wrapped in outcome-producing catches. This suite
has no direct retained/external skip, unsupported-operation, missing-interpreter/
authorizer, grant-mismatch matrix, concurrent allocation, status-write failure or
post-commit success-evidence failure test.

Security evidence is limited to the tested grants, selected authority/reference
admission and explicit failure/evidence canaries. The adapter's selected preflight
paths still use str(error), and bounded evidence is not universal value redaction.
Operational evidence is selected ingress/reference persistence plus returned
outcomes, not complete session/action/activity history. No assertion here establishes
restart safety, provider ownership, adoption, retry eligibility or the deletion of
real test resources.

Reading depth is the complete test file and 808-line adapter, previously read full
Postgres unit of work and canonical timestamp helper, and selected ingress values/
store, secret grant/receipt/admission/store, evidence and coordinator contracts.
Those dependencies were not all audited in full. The source companion is a separate
review slice; this note changes no source, fixtures, inventory or execution policy.
