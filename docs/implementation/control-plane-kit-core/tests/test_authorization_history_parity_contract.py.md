Source: [control-plane-kit-core/tests/test_authorization_history_parity_contract.py](../../../../control-plane-kit-core/tests/test_authorization_history_parity_contract.py).
Maintain this document alongside its source file. Update it when operation
coverage, authorization/safety/history expectations, descriptor checks or error
policy assertions change.

This 310-line suite contains five tests and three local construction helpers.
It builds security-policy values over read and command adapter contracts. It
does not authenticate a caller, invoke HTTP/MCP, persist history or inspect a live
error response. Its public imports come from the Core operations facade, with a
local descriptor assertion helper for selected disclosure checks.

## Composed fixture and selected coverage

_program creates a service binding for every current role. _transaction_rule
declares READS read-only, AUTHORIZATION without store participation, EXECUTION
read-write with transaction ownership/after-commit effects/worker/runtime
authority, and the remaining roles read-write with transaction ownership.
_security_parity combines the default MCP contract, canonical HTTP read and
command routes, their parity values and that unit-of-work contract. These are
declarations; constructing them does not open a transaction or start a service.

The first test fixes the total operation count at 74, then looks up selected
operation IDs. It checks these auth/safety/history combinations:

- deployment.execute: EXECUTION_RUN, DESTRUCTIVE, accepted-and-rejected history,
  and BOUNDED_REDACTED error disclosure.
- read.workspace, read.session-actions, read.run-events, read.approval-detail,
  read.runtime-authorities and read.ingress-authorities: READ, READ_ONLY and
  NOT_RECORDED history.
- product-descriptor.import, runtime-authority.register and
  ingress-authority.register: ADMIN, COMMAND and accepted-and-rejected history.
- approval.request and deployment.prepare: PLAN_WRITE, COMMAND and
  accepted-and-rejected history.
- graph.advance-current and run.start: EXECUTION_RUN, COMMAND and
  accepted-and-rejected history.

The count is literal, but the test does not pin all 74 identities, their order or
every policy on every operation. The error-disclosure field is directly asserted
only for deployment.execute in this positive test. NOT_RECORDED for a read means
the declared command-history policy; it is not a claim that transport logs or
all audit systems record nothing when the read occurs.

The actual [parity owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py)
builds read security entries with READ_ONLY/NOT_RECORDED, command entries with
route safety and accepted-and-rejected history, and all entries with
BOUNDED_REDACTED. Auth scopes come from their respective route contracts. Its
aggregate requires matching MCP contract values, unique operation IDs and exact
coverage of the union of supplied projection/command IDs, then validates entries
against those bindings and sorts them. Those are source-context laws; this suite
does not independently exercise each with a negative case.

## Descriptor reconstruction and benign disclosure checks

The second test fixes the outer adapter-operation-security-parity kind and
round-trips its computed mapping. It excludes token, password and private_url
from the lowercase repr and invokes the
[shared descriptor helper](../../../../control-plane-kit-core/tests/contract_security_assertions.py).
That helper recursively visits mappings and non-string/non-bytes sequences,
rejects a fixed set of normalized raw-secret field names and searches lowercase
repr for five known secret-value markers. The test then rejects one extra outer
key with InvalidAdapterParityContract.

This uses benign factory metadata, without injected credentials or transport
errors. It is neither universal redaction evidence nor a byte/count/depth bound.
The mapping round trip is not an independent canonical byte/hash vector, and
the single extra-key case is not an exhaustive schema-admission matrix. No test
asserts error text, repr, size or cause/context chains. Selected owner decoders
retain ValueError text and causes; BOUNDED_REDACTED names a downstream policy,
not the behavior of every exception raised while decoding this contract.

## Four changed-policy candidates

The last three tests rebuild the complete operations tuple, preserving every
entry except one selected field on one selected operation:

| Candidate | Changed field | Expected result |
| --- | --- | --- |
| read.workspace | auth scope becomes ADMIN | InvalidAdapterParityContract |
| deployment.execute | safety becomes COMMAND | InvalidAdapterParityContract |
| deployment.plan | history becomes NOT_RECORDED | InvalidAdapterParityContract |
| deployment.execute | error disclosure becomes TRANSPORT_PRIVATE | InvalidAdapterParityContract |

These changes remain typed AdapterOperationSecurityBinding values; the aggregate
detects their disagreement with the composed contract. In the first case, the
actual projection guard checks agreement with the route's READ scope before its
separate READ-only scope requirement. That candidate contradicts both; the test
asserts neither diagnostic precedence nor a distinct altered-route case.

The second candidate exercises command safety agreement with the destructive
execute route. The third exercises required command-history metadata. The fourth
exercises the aggregate requirement that every operation's error-disclosure
policy be BOUNDED_REDACTED. Despite the test names, there is no separate negative
changing a read's safety to COMMAND, no journal append, and no real error payload
tested for bounded or redacted output. These are selected contract rejection
cases, not all combinations of auth, safety, history and disclosure values.

## Ownership and evidence limits

The structure is security metadata derived from read/command bindings and checked
for consistent composition. Auth scope labels do not establish actor authority;
history requirements do not persist accepted/rejected actions; error policy
labels do not redact an exception. Those behaviors require evidence from the
owning services and adapters. No permission to mutate, retry or deploy follows
from this test or its descriptors.

Authoring read all 310 lines, all five tests/three local helpers and the full
shared descriptor helper, plus selected actual parity security-binding,
aggregate, validation, decoder and factory sections. Retained command-parity
and transaction context explains the fixture. The 1,407-line parity owner and
other tests do not gain coverage from this note. No source changes, application
imports, executable tests, builds, live actions or merge occurred. Documentation
links, whitespace and the frozen source/test guard were checked; independent
review is pending.
