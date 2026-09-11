Source: [control-plane-kit-operations/tests/effect_attempt_intent_fixture.py](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 535-line fixture supplies constructed runtime-effect intents, original start
events, protected intent records, hostile candidates and assertion helpers. It
has no test methods, database fixture or provider adapter. EffectAttemptIntentFixture
is a plain mixin: helpers that assert results depend on a unittest-compatible
consumer, while ordinary intent construction is also used directly by other
fixtures. The file's constants pin the private evidence owner, one event ID,
the 1,048,576-byte intent ceiling and the expected categorical error string.

At import time _load_optional attempts the exact intent-evidence module. It
returns None only when ModuleNotFoundError.name equals that requested module;
missing nested dependencies escape. Module globals then capture the record and
two private codec functions through getattr with None defaults. This is not a
general import-error suppressor or an automatic retry after installation.
require_intent_language asserts all three captured names are present; it does
not skip tests. The selected
[contract consumer](../../../../control-plane-kit-operations/tests/test_effect_attempt_intent_contract.py)
checks that an injected nested-module failure escapes as the same object.

assert_intent_error requires OperationsRecordError, the exact message "effect
attempt intent evidence is invalid", absent cause/context, combined str/repr of
at most 256 characters and absence of each supplied canary's string. It uses
assertRaises rather than an exact exception-type assertion. The helper expresses
a law for its callers; defining it does not establish that every candidate or
boundary has been tested.

forge_exact allocates an instance without its constructor and writes only the
provided fields with object.__setattr__. subclass_copy creates a dynamic subclass
and calls its constructor using the original dataclass's init fields. These are
different apparatus: one bypasses validation, while the other can fail during
construction. class_access_hostile_copy also bypasses construction, copies all
dataclass fields and traps __class__ reads into a supplied dispatch list.

deep_coordinate_intent_candidates returns four labeled forged exact intents:
hostile RunId wrapper, exact RunId containing hostile text, hostile ActivityId
wrapper and exact ActivityId containing hostile text. Wrapper traps cover
__class__, value, equality and hashing; text traps cover __class__, equality,
hashing, strip and encode. Every trap records a dispatch then raises AssertionError.
Each candidate keeps the remaining source/intent fields from the supplied value.
The builder itself neither invokes an admission boundary nor asserts no dispatch.
The selected consumer exercises encode and record boundaries, checks empty
dispatch lists and forbids public request projection for these deep candidates.

ClassAccessHostileBytes traps __class__, len and decode, with a class-level
dispatch list. Consumers must reset that shared list when isolating a case; the
fixture does not automatically clear it. The selected codec test resets it and
checks rejection before any trap runs. None of these classes is a sandbox or a
universal hostile-object detector: they expose selected dispatch opportunities
for explicit tests.

authority_delivery constructs a remote-docker reference with a remote Docker TLS
secret-file delivery and three labeled secret references: CA certificate, client
certificate and client key. The actual
[authority values](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py)
normalize typed references/deliveries and validate their structure. These are
declarations of access material, not TLS credentials, authorization grants,
resolved file contents or authenticated Docker access.

product_material constructs a Docker hello-server product with synthetic image
digest/tag/provenance, HTTP and PostgreSQL provider ports, an application-control
secret environment delivery and a PostgreSQL readiness query using a password
reference. Its RuntimeProductMaterial adds public and socket-derived environment
values plus an OCI pull credential reference. Public/socket values and node ID
can be varied by callers. The fixed descriptor digest is a test value, not a
computed registration receipt. The actual
[material owner](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
checks matching product identity and typed material; no registry pull, product
registration, socket resolution or readiness query happens in this builder.

intent first creates that delivery, selects either one default product or the
supplied tuple, then replaces every product's runtime_authority_deliveries. Only
an api product receives the delivery when process_delivery is enabled; other
products receive an empty tuple, including any caller-supplied prior declaration.
The replacement reconstructs frozen product material instead of mutating it.
The request targets StartNode(api), or StopNode(api) for compensation, with fixed
Docker/workspace/plan/graph coordinates and configurable request/run/activity IDs.
The effect ID and source intent-event ID initially equal EVENT_ID.

Request-level authority delivery is present only for non-compensation with
process_delivery enabled, while authority_ref remains present in all cases.
A compensation product can still carry its declared delivery even though the
StopNode request delivers none. The actual recipient validator forbids request
deliveries on non-start/reconcile operations; for a start with deliveries it
requires exact target material and matching declarations/authority. A custom
product collection that violates these contracts can fail during construction;
the fixture does not guarantee every parameter combination is lawful.

The request is projected through the actual
[core intent transformations](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py).
Projection removes the generated event ID and transient resolution grants from
the pre-start intent while preserving its durable coordinates/material. The
inverse binds a supplied event ID and grant tuple to a new request. These are
pure constructors and transformations, not interpreter execution.

identity constructs attempt one with a canonical RunId and an activity string.
original_event creates ordinal three at a fixed 2030 timestamp, choosing
STEP_STARTED or STEP_COMPENSATION_STARTED from its compensation flag. It takes
run/activity from the intent and inserts bounded evidence containing attempt one
and an all-a state fingerprint. That fingerprint is synthetic; no attempt state
is hashed and no preceding journal is created. If a caller supplies an intent,
the event's compensation flag is still independently selected.

record requires the optional language and calls the actual
[EffectAttemptIntentRecord](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
with supplied-or-default identity, event and intent. A default identity remains
run-a/start-runtime even when a custom intent changes those coordinates, so the
caller must supply a congruent identity. The owner checks exact nominal types,
reconstructs values, round-trips canonical intent bytes and binds run/activity
across identity, event and intent. It permits only the two start-event kinds.
Those checks do not authenticate the event or verify the fixture's placeholder
state fingerprint against a persisted attempt. The record hides event and intent
from its default repr; its referenced material is still protected operational
data rather than a generic public projection.

canonical_bytes serializes the descriptor with rfc8785.dumps; it does not call
the private encoder or independently enforce the size limit. public_round_trip
reconstructs a request with an empty grant tuple and asserts that tuple plus
effect_id/source event-ID agreement, then returns the reprojected intent. It
does not itself assert equality with the original intent or fingerprint; callers
provide those assertions. The private owner additionally admits only canonical
bytes, rejects duplicate JSON keys/nonfinite constants and checks exact top-level
and source shapes, using the same one-mebibyte ceiling.

largest_lawful_intent first allocates 2,048 products with distinct long node IDs,
then binary-searches prefixes whose canonical descriptor fits the ceiling, with
process delivery disabled. It returns the largest accepted prefix found in this
specific family, not a globally maximal intent or an exactly 1,048,576-byte value.
The initial product allocation also means the byte ceiling is not a total memory
bound. Selected consumers assert size above half the ceiling and at most the
ceiling, public round-trip equality and private codec admission.

Consumer relationships extend beyond the focused contract file. The
[attempt-record fixture](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
and [start fixture](../../../../control-plane-kit-operations/tests/effect_attempt_start_fixture.py)
derive request fingerprints from constructed intents at module import time. The
[PostgreSQL intent-store fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_intent_store_fixture.py)
reuses the large-intent builder and constructs state/event/intent triples with a
matching actual intent fingerprint. Its persistence belongs to that consumer's
apparatus. These selected crossreads explain dependencies without claiming full
review or execution coverage for those files.

Security evidence provided by this fixture is explicit error/canary apparatus,
typed reference-only material and hostile-dispatch candidates. Its synthetic
references do not retrieve secrets or grant provider powers, and its constructed
event/record is not persisted history. No database/provider mutation, process
restart, compensation execution or resource cleanup is performed here.

Read depth: all 535 fixture lines/helpers, full 265-line intent-evidence owner,
actual core intent projection/inverse/fingerprint paths, product and delivery
contracts, and selected consumer assertions/fixture integrations. No application
imports, tests, database calls or provider actions were executed during authoring.
