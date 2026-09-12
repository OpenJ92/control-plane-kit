Source: [control-plane-kit-operations/tests/test_execution_lease_recovery_support_contract.py](../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_support_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These two tests protect the private interface and ownership of
[shared lease-recovery support](../src/control_plane_kit_operations/_execution_lease_recovery_support.py.md).
They inspect imports, signatures, root attributes, source AST and the module
inventory. They do not call the approval, journal or retry-evolution functions,
instantiate stores, open PostgreSQL or execute a recovery. The word total in the
first test's name does not make it behavioral coverage for every input.

The module-level import guard turns absence of the exact support module into
support=None so require_support can fail with a targeted assertion. A different
ModuleNotFoundError, such as a missing nested dependency, is re-raised. This guard
does not suppress arbitrary import failures or provide a fake implementation.

The first test requires the three helper names and compares the ordered parameter
names from inspect.signature: stores/request for locked_recovery_approval;
decision_kind/expected_fence/run/plan/events for journal eligibility; and
stores/request/retained_run for replay evolution. It does not assert parameter
kinds, annotations, defaults or return types. It expects getattr(__all__, ()) to
equal (), and requires the three helpers to be absent from the Operations root.
Because the export check has a fallback, it alone would not distinguish a missing
__all__ from an explicitly empty one; the actual owner declares it empty.

The second test reads CPK_PACKAGE_MODULE_INVENTORY when supplied, otherwise the
repository's [package module inventory](../../../architecture/package-module-inventory.json).
It requires exactly one row for the support module, the operation owner label,
the same canonical destination, no public exports or optional external dependencies,
and this test in protecting_tests. Those are selected metadata assertions. They
do not verify every inventory field, enumerate the whole filesystem, establish
acyclicity or prove that runtime code never uses an external capability.

It parses the actual
[lease-recovery interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py)
and requires an ImportFrom of the support module. It then requires one interpreter
inventory row whose internal_dependencies set equals its control_plane_kit-prefixed
ImportFrom module names. This comparison ignores ordinary Import nodes and dynamic
imports; it is not a full import-graph audit. The AST must also contain no function
definitions named _approval, _require_journal or _journal_without_recovery_pairs,
preventing those named predecessor implementations from remaining there. It does
not detect every possible renamed duplicate or assert dynamic calls to the helpers.

The current source supplies the rationale: both recovery interpreters share approval
linkage, journal admissibility and linked-run replay checks, while durable mutation
and transaction ownership remain in callers. This test directly inspects the
lease-recovery predecessor, not both callers' complete behavior. Selected runtime
delegation coverage elsewhere must remain separate from this static contract.

The neighboring
[retry-totality tests](test_execution_lease_recovery_retry_totality.py.md)
exercise two actual journal rejection cases. Neither test file proves live lease
expiry, transaction atomicity, permission admission, persisted replay history or
provider/runtime safety. These are small complementary boundaries, not a combined
acceptance suite for recovery.

Read depth: full 131-line test, retained full 390-line support owner and selected
actual caller/inventory context from its companion review. The 156-line neighboring
totality test was also read fully for this pair. No source/pin changes, executable
tests, database setup, credentials/private-key access, provider/runtime actions or
publication occurred. Documentation adds no new security surface and records no
claim that these assertions ran or passed during authoring.
