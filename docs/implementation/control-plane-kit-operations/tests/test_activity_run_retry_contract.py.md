Source: [control-plane-kit-operations/tests/test_activity_run_retry_contract.py](../../../../control-plane-kit-operations/tests/test_activity_run_retry_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These 12 tests protect the pure
[retry command](../src/control_plane_kit_operations/activity_run_retry.py.md),
imported retry-counter/recovery-fence laws and module ownership. Six language tests
cover command shape, fingerprint and invalid inputs; four cover record/evidence
laws; two inspect inventory/import boundaries. They construct Python values and
read source/metadata. They do not execute the retry interpreter, open a database,
claim a request, write history or dispatch an effect.

The fixture builds request-a, a typed prior RunId, worker-a generation-7 fence,
RecoveryAuthority with operate scope and authority reference, and a retry
IdempotencyKey. _load_retry_module returns None only if that exact module is absent.
The dedicated guard test injects a missing nested dependency and a partial ImportError,
requiring those failures to propagate rather than be hidden as absent retry code.
It does not replace the command implementation with a mock.

The shape test requires Operations-root export identity with the owner class, a
frozen dataclass and the exact five field names. A subclass of RetryFailedActivityRun
must be rejected. It does not require slots or prove every nested value is deeply
immutable. The descriptor test compares the entire expected six-key dictionary:
command, request/prior-run IDs, expected fence, actor and idempotency key. Combined
command repr/descriptor must omit the fixture authority reference and selected
lease/secret/endpoint strings. Scopes must be absent from the descriptor but present
in command repr. These are selected representation laws, not universal serializer
redaction or credential validation.

Four hard-coded SHA256 golden vectors fix the fingerprint for the baseline,
changed request, changed fence generation, and a combined changed prior/actor/
authority-reference command. A separate test changes request, prior run, fence
worker, fence generation, actor and reference individually and requires distinct
fingerprints. Adding a recovery scope or changing the idempotency key preserves
the fingerprint. The actual owner uses sorted compact standard JSON, not RFC8785;
these ASCII fixtures do not exhaust Unicode serialization. The reference is hashed
but hidden from descriptor/repr. No digest is treated as authentication or permission.

Fifteen invalid command cases cover empty/None/bool/int/subclass/oversized/newline
request text, raw or subclass prior IDs, dictionary/subclass fences, raw/subclass
authorities and raw/subclass idempotency values. Every case must raise
InvalidOperationCommand, have no cause/context, fit 512 characters in combined
str/repr and omit the supplied nonempty canary. This is a selected negative matrix,
not every field/type/length combination. It does not test acceptance of the maximum
512-character request or enforce OPERATE scope membership through a service.

The retry-counter test exercises actual
[RetryIdentity](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py):
first attempt with no predecessor and maximum PostgreSQL integer attempt with a
predecessor are admitted. Seven invalid counter/predecessor shapes include bool,
float, int subclass, zero/negative, a predecessor on attempt one and a missing
predecessor on attempt two. Maximum+1 must produce the exact bounded domain message.
These are constructor laws; no PostgreSQL schema or counter allocation is queried.

Recovery evidence tests require RETRY_AS_NEW_RUN to retain the same fence on both
sides, including generation 2**63-1. Six negatives cover absent replacement,
changed worker/generation, hostile prior/replacement fence subclasses and a
hostile RunId subclass. They require bounded, chain-free OperationsRecordError
with selected canaries absent. The unchanged fence is a retry semantic law, not
proof that the represented lease has not expired.

One compatibility test constructs accepted renewal, takeover and abandonment
evidence: same-worker generation increment for either renewal, different-worker
increment for takeover, no replacement for abandonment. This confirms four positive
forms remain admitted alongside retry. It is not a complete regression matrix for
every invalid older recovery transition and performs none of those operations.

The ownership test reads the configured inventory path or the default
[module inventory](../../../architecture/package-module-inventory.json), requires
one row for the owner with operation ownership/canonical destination, no optional
external dependencies, exactly the two public exports and exactly the two governing
test paths. The final AST test rejects import names containing postgres, stores,
unit_of_work or interpreter. It inspects ordinary Import/ImportFrom syntax, not
dynamic imports, all transitive dependencies or runtime IO. These checks preserve
the language/interpreter boundary without proving the full package graph.

The [result tests](test_activity_run_retry_result.py.md) separately validate linked
record coherence. Neither file establishes current approval, active authority,
journal eligibility, transaction atomicity, retry history persistence or safe
re-execution of a failed effect. Those belong to interpreter/store/integration tests.
The error helpers cover expected validation failures, not all exceptions from
arbitrary nested objects or callers.

Read depth: full 505-line test and fixtures, full 299-line owner and 671-line
neighboring result test retained from owner review, with actual authority,
idempotency, RunId and retry/evidence record contracts. No source/pin changes,
executable tests, database setup, credentials/private-key access, provider/runtime
actions or publication occurred. Documentation adds no security surface and does
not claim this suite ran or passed during authoring.
