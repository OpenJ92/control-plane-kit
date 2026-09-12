Source: [control-plane-kit-operations/tests/test_execution_lease_recovery_contract.py](../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests protect the pure
[recovery language](../src/control_plane_kit_operations/execution_lease_recovery.py.md),
imported recovery-evidence/event laws and selected module ownership. Eight methods
exercise language/record behavior and one reads inventory. They construct actual
Python values; no UoW, database, interpreter execution, lease observation or
provider action occurs in this test file.

_load_recovery_module treats only absence of the exact owner module as None.
The dedicated import-guard test injects a nested ModuleNotFoundError and requires
the same object to escape, then checks that a partial ImportError also propagates.
The guard does not replace missing production classes with test implementations.
require_language explicitly reports missing owner/evidence names.

The common command fixture uses request-a, typed RunId run-a, worker-a generation
seven, a renewal-scope authority, a 600-second duration where applicable and an
idempotency key. Its authority is reused for takeover and abandonment commands
without substituting their operation scopes. This reflects pure value admission:
these tests do not authorize takeover/abandonment or authenticate the reference.
Evidence fixtures independently construct same-worker generation eight for either
renewal, different-worker generation eight for takeover and None for abandonment.

The public-shape test checks root-export identity and frozen dataclass status for
authority, the four concrete commands and imported evidence. It compares the
command union to those four variants and the authority/evidence field names to
their exact shapes. The command-field test separately checks all four concrete
field lists, complete descriptor key sets and each command discriminator. It
does not compare every descriptor value in that test. Authority-reference key
text and scopes must be absent from the descriptor.

Authority normalization is tested with a 512-character actor and repeated,
out-of-order scopes, requiring deduplication and enum-value order. The reference
canary must be absent from repr and its dataclass field must have repr=False.
Each of actor/reference receives five invalid inputs: empty, newline, 513-character,
integer and None; three malformed scope collections cover strings/mixed tuple/list.
Expected InvalidOperationCommand errors are chain-free, bounded to 512 combined
str/repr characters and omit supplied nonempty canaries. The test does not exhaust
Unicode/byte limits, every string subclass, or the maximum-length reference.

Twelve invalid common-command examples exercise active renewal: malformed request
text, raw/subclass RunId, raw/subclass fence, raw authority, raw/subclass idempotency
key and raw/subclass duration. Five takeover examples reject the prior worker,
empty/newline/oversized/non-string next worker. Dynamically created subclasses of
all four commands must also be rejected. These are selected nominal boundaries,
not every invalid field crossed with every command. They do not prove recursive
exact typing inside admitted fence/key wrappers or exercise authority subclass
rejection.

Fingerprint tests specify four complete intent documents, then compute expected
SHA256 using sorted compact standard json.dumps and compare each command digest.
The expected digest is computed during the test rather than supplied as a fixed
golden vector. The documents include authority reference and applicable duration/
next worker, excluding scopes and idempotency key. A takeover reference change must
change its digest; changing scopes or key must preserve it. The title's excluded
retry-authority wording does not mean authority_reference is excluded. There is
no separate mutation matrix for every included field, Unicode canonicalization
proof or RFC8785 implementation here, and hashing does not establish permission.

The evidence test checks descriptor keys and selected values for the four recovery
forms, five invalid decision/transition combinations, four raw nominal input forms
and three RunId/fence subclass forms. It admits renewal from generation MAX-1 to
MAX, then rejects attempted renewal from MAX to MAX. The actual imported
[ExecutionLeaseRecoveryEvidence](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
also supports retry-as-new-run with an unchanged fence; this older four-form matrix
does not assert those are the only decisions the shared evidence owner can admit.
The separate retry tests govern that additional form. These are value laws, not
proof of a live worker lease, generation allocation or expiry classification.

The event test accepts recovery evidence on RECOVERY_DECISION_RECORDED with empty
general evidence/no failure. Five negatives reject missing recovery evidence,
a foreign run, simultaneous failure, duplicate general evidence and recovery
attached to RUN_OPENED. An ordinary RUN_OPENED still has recovery=None. Actual
event constructors supply these intrinsic exclusivity laws before a result can
validate cross-record correspondence. Several evidence/event negatives assert
only the error class, without calling the bounded-error helper.

The ownership test reads the configured path or default
[module inventory](../../../architecture/package-module-inventory.json), requiring
one owner row, operation ownership, canonical destination, no optional external
dependencies and membership of both governing tests. It does not assert an exact
protecting-test set, all public exports, all dependencies or an acyclic import
graph. There is no AST import-boundary test in this file.

The [result tests](test_execution_lease_recovery_result.py.md) separately validate
record linkage, request/run states and action payloads. Neither pure file proves
scope enforcement by a service, approval validity, journal eligibility, transactional
history, concurrent recovery or safe effect execution. Reference hiding is limited
to the tested representation; fields remain accessible to application code.

Read depth: full 688-line source, all nine methods and fixtures/helpers, full
442-line owner and 513-line neighboring result tests, with actual authority,
fence/duration/key/evidence/event contracts and retained interpreter context.
No source/pin changes, executable tests, database setup, credentials/private-key
access, provider/runtime actions or publication occurred. This companion adds no
security surface and does not claim these tests ran or passed during authoring.
