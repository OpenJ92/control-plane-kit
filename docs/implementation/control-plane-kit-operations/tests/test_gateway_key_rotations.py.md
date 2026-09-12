Source: [control-plane-kit-operations/tests/test_gateway_key_rotations.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotations.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The 13 tests exercise
[rotation service](../src/control_plane_kit_operations/gateway_key_rotations.py.md)
and [PostgreSQL persistence](../src/control_plane_kit_operations/postgres/gateway_key_rotation_store.py.md)
through a required test database and fresh UoWs. setUp installs/verifies the
schema, truncates workspaces CASCADE and seeds workspace-a. Even the tests using
fail-on-access fakes inherit this database setup. Running this file is therefore
database-mutating validation requiring an isolated test database, not a pure
unit-test command. No tests were executed for this documentation change.

Request laws cover same-intent correlation replay, conflicting intent under the
same correlation, and exclusion of a second nonterminal rotation for one
workspace/node/purpose/issuer binding. A second purpose test admits
WORKLOAD_NODE_CONTROL_SURFACE_READ, checks its stored rotation approval subject,
descriptor and review digest, and verifies that schema installation remains
idempotent. Scope/version negatives deny a generation-only caller's rotation
request and stale expected rotation version.

Transition replay preserves the result without duplicating a transition and
rejects changed approval identity under the same transition ID. Another test
counts the epoch-clock call on a first transition and supplies a clock that
would fail on replay; identical replay succeeds without consulting it. This is
ordinary awaiting-approval replay, not deployment replay after a changed fence,
nor replay after several later state transitions.

The concurrent request test submits two different correlations/replacement
references to a two-worker ThreadPoolExecutor and requires exactly one success
and one GatewayKeyRotationConflict. It uses real stores but has no barrier or
database lock-wait observation forcing their transactions to overlap. It does
not exhaust different bindings sharing a correlation, competing deployment
acceptance, or worker-lease turnover races.

The [shared overlap fixture](../../../../control-plane-kit-operations/tests/gateway_rotation_overlap_fixture.py)
seeds registered product metadata, an authored graph with two gateways, realized
verifier projections, synthetic public PEM strings and signing-key reference
records. It creates actual operation/approval records and advances generation
using supplied provider/action/version identities. No key provider generates
those fixture keys. prepare_fenced_overlap truncates/reseeds again, then uses
the actual overlap preparation program with plan/rotation/operator scopes and
a worker execution lease to obtain a prepared checkpoint and handoff.

accept_prepared_overlap drives the actual overlap execution program once per
planned activity through an ExecutionCoordinator with SuccessfulAdapter. That
adapter returns synthetic activity/runtime success; it does not contact Docker
or a gateway. The fixture wires an indeterminate observer for reconciliation,
but these happy-path assertions do not prove an uncertain-effect recovery.
Other fixture crash wrappers exist but this file does not invoke them.

The overlap/drain test accepts that prepared deployment, records activation,
checks the deadline equals injected epoch plus 60 seconds lifetime and 5 seconds
skew, advances to draining, and reads back the same durable state. It checks
selected transition positions and the read model's absence of the word secret
in repr plus its new key ID. It does not sleep, test the retirement deadline
boundary, inspect outstanding grants or complete retirement/provider revocation.
The read-model assertion is a narrow omission check, not a comprehensive
redaction/property test for all operational identities and error paths.

The blocked test uses advance_deployment with the prepared fence, retaining the
exact child checkpoint and failure code. An ordinary advance attempting guessed
overlap-ready evidence conflicts. That proves the ordinary writer rejects this
shape; it does not cover every forged accepted-evidence field through the fenced
writer. The restart-named test constructs a fresh service/UoW and reconstructs
the prepared checkpoint/history, then verifies SQL rejects run/bad under the
named deployment run-ID constraint and preserves the checkpoint. It restarts
neither a process nor a database/provider.

Temporal laws use a syntactically shaped but impossible calendar date. The
service fail-on-UoW test rejects request, transition, activation, retirement,
revocation and nested checkpoint times before access. The direct-store
fail-on-execute cases cover add, CAS main/nested timestamps and transition INSERT
before any SQL. Invalid duplicate request/transition inputs cannot bypass time
admission through replay and leave existing rows intact. A hostile str subclass
is rejected without invoking its hooks; the asserted timestamp error is bounded,
contains no marker and has no cause/context. That assertion applies to this
timestamp path, not all imported or lookup exceptions in the service.

Approval helpers create durable sessions and use ApprovalCommandService for
rotation review requests and decisions with separate rotate and rotate-approve
scopes. The file's ordinary transition helper supplies those resulting IDs;
approval is not simulated by attaching arbitrary success flags. Other supplied
generation/revocation/checkpoint metadata remains synthetic. Full rejection,
completion, provider-custody validation, live key use and cleanup are not proved
by this file's assertions.

Read depth: full 731-line test file, full 620-line fixture, full 1,381-line owner
and 357-line store. Selected actual Core approval subject, advancement
result/validators, worker/fence and SQL constraints were also inspected. The
overlap programs were traced through fixture call sites, not claimed as a full
review of those separate owners. No credentials, provider effects or runtime
actions were used; this documentation adds no new security or mutation surface.
