Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_generation_program.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_generation_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests exercise the
[generation program](../src/control_plane_kit_operations/gateway_key_rotation_program.py.md)
against real PostgreSQL with directly constructed generation evidence. setUp
requires CPK_OPERATIONS_TEST_DATABASE_URL, installs/verifies schema and truncates
workspaces CASCADE. It creates a workspace, registers a provider, starts a session,
requests/decides a real rotation approval and advances the rotation to APPROVED.
tearDown closes the main connection; the next setUp resets data. These tests
mutate an isolated database, and none were executed for this documentation.

The setup has no deployment graph, gateway runtime or registered old key. Its
requested old-key ID is rotation intent, not an observed active signer. Provider
registration uses synthetic endpoint/credential references, a keys prefix and
gateway-signing intent. An operator requests rotation and a manager with
DELEGATION_KEY_ROTATE_APPROVE supplies its actual durable approval decision.
That establishes the approved generation starting state, not deployable topology,
an independently approved exact provider version or provider custody.

Preparation commands carry ROTATE and GENERATE scopes; submission carries ROTATE
and REGISTER, all DELEGATION_KEY_* values. IDs and textual times are deterministic,
and the rotation service gets epoch 1000. The evidence helper constructs
DelegationKeyGenerationEvidence with gateway-key-b, Ed25519-shaped public PEM,
version-b / 1, the action's reference/correlation and replayed=False. It neither
calls a generator nor receives an authenticated provider receipt. Core validates
PEM framing rather than parsing/generating a cryptographic key pair. There is no
fake provider cache or durable provider implementation in this focused file.

The first test prepares, creates a fresh program and prepares again with a
different caller timestamp. Actions must compare equal, rotation must be
GENERATION_PREPARED, stored provider/digest must match the action, and grant
reference/correlation must match the rotation. repr(action) must not contain the
substring private. Source reconstruction uses the original preparation transition
time. This is stable-truth object reconstruction, not a process kill or proof
that every action field remains immutable under provider/actor drift. The repr
assertion does not audit descriptors, full reference exposure or exception logs.

The success test submits constructed evidence twice. First returns KEY_GENERATED
with replayed=False; the second returns the same rotation with replayed=True.
The rotation key ID is gateway-key-b; exactly one active reference and exactly
that one verification key exist. These assert selected durable cardinality and
terminal replay, not provider exactly-once generation or equality of every action,
timestamp, stored public-key field and transition. The test does not assert a
provider call count because no provider is invoked.

The provider-success-before-fold test merely retains the constructed evidence,
reconstructs the same action through a new program, changes evidence.replayed to
True and submits it. It requires action equality and KEY_GENERATED. There is no
provider mutation, receipt replay cache, thrown process-loss exception or actual
restart. The flag is supplied evidence, not a measured provider recovery result.

The admission-before-rotation-fold test first calls the real
[generation admission service](../src/control_plane_kit_operations/delegation_key_generation.py.md)
directly to persist the reference/key, then submits evidence with replayed=True
through the program and requires KEY_GENERATED. This manually establishes one
partial durable prefix and exercises registration replay before final rotation
fold. It does not interrupt a transaction, inject a crash, vary admission identity
or enumerate every persistence boundary. No provider compensation is tested.

The failure test first submits DEFINITE_FAILURE and requires the prepared
rotation plus the exact same next_action. It then submits UNCERTAIN and requires
BLOCKED with no next_action. Repeating the same uncertainty returns the same
rotation; a different uncertainty code must conflict. It does not actually call
a provider again after definite failure, verify that the provider made no mutation,
or assert a durable per-attempt failure record. It also does not test success or
definite-failure submission after the block.

After a successful fold, replay drift tests alter both action version coordinates
while retaining their required offset, then separately change the evidence's
secret reference. Both must conflict. These protect selected terminal lineage
checks; they do not test every field. In particular the source's generated replay
compares public key ID rather than PEM/fingerprint and does not reload current
key/reference/provider rows. Changed public material under the same ID and stale
provider truth on terminal replay are not covered by these assertions.

The final test rejects stale preparation version, a fresh unapproved rotation
with another gateway/correlation, and an inactive provider. Provider inactivity
is created through the real local registration service's revoke_provider command;
it is not a provider network revocation. The cases assert exception type but do
not compare all tables or exact messages/cause/context. They do not exercise
scope-denial constructors, actor differences, custody prefix/intent changes after
preparation, missing/tampered prepared transition or concurrency.

The actual program leaves provider IO to its caller; preparation persists only
provider/digest anchors in rotation history, and fresh admission registers
reference/key atomically before a separate rotation transition. The tests' real
approval and database service calls are meaningful evidence for those paths,
but cannot certify external idempotency, private/public correspondence or durable
provider receipts. Broader program-acceptance, deployment, activation, revocation
and cleanup validation remain separately scoped.

Read depth: full 452-line test and 615-line program, retaining reviewed full
generation406, rotation and provider/key contexts. Actual generation preparation/
admission, custody fingerprint/reference candidate, earlier approval transitions
and Core public-key construction were inspected. No tests, key/credential,
database/provider/runtime actions or source changes were performed for these
notes. The documentation introduces no security or durable-mutation surface.
