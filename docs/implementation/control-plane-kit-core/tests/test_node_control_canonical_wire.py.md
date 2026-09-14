Source: [control-plane-kit-core/tests/test_node_control_canonical_wire.py](../../../../control-plane-kit-core/tests/test_node_control_canonical_wire.py).
Maintain this document alongside its source file. When canonicalization, numeric admission, fixture assertions or dependency assumptions change, verify and update this companion in the same change.

This 195-line suite has eight tests. Helpers create nominal graph references, a
scalar apply-command request and a two-target weighted state with one varied
weight and one fixed positive weight. The tests cover selected canonicalization
and numeric laws of [node_control.py](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py);
they do not sign, authenticate, dispatch or mutate workload state.

## Fixed and computed evidence

The identity test requires `jcs-rfc8785.v1` in one request descriptor, checks a
root `NodeControlCanonicalization` attribute exists, and rejects a missing or
different canonicalization field through mapping decode. It does not assert the
root attribute's object identity or every invalid field form.

The [fixture](fixtures/node_control_canonical_wire_v1.json.md) test checks its
schema and exact three consumer labels. All three request descriptors are
decoded, then canonical UTF-8 text, hex and computed request digest are compared
with fixed fixture literals. Unlike comparing two calls to the same hash method,
this provides stored expectations. It does not independently establish where
the expectations came from or execute the named Java/C++ consumers. This suite
does not read the fixture's workload-grant section or separately assert its
top-level canonicalization label.

Five binary64 bit-pattern vectors are reconstructed using `struct.unpack`, put
into scalar requests and checked with `assertIn` for a `"value":...` substring.
This does not compare an isolated numeric token or an entire expected request
for those five cases. A separate test compares requests built from `1` and
`1.0`: object equality, computed canonical bytes and computed digests agree.
That is one representation-equivalence example, not a general proof over all
equal numeric values.

## Numeric boundaries

- Negative zero is rejected through scalar, map-value and weighted-state
  constructors. The test title's “every state boundary” refers to those three
  selected paths, not every decoder/result/field combination.
- One negative integer weight is rejected. For `2**53` and `10**400`, scalar,
  map-value and weight construction must raise the contract error; an accidental
  `OverflowError` would not satisfy that expectation. Both values also construct
  grants with oversized issued/not-before/expiry fields together. The actual
  constructor checks issued-at first, so this does not independently isolate
  each epoch field's rejection.
- Scalar requests at both signed safe-integer endpoints, `1e20` and `1e-7`
  round-trip through descriptor mappings and produce bytes. The maximum safe
  integer weight is observed normalized to its float equivalent. These cases
  do not exhaust NaN/infinity, boolean, subclass, negative-zero wire spelling,
  size or error-redaction behavior.

Actual scalar admission distinguishes exact integers from exact floats:
integers have the safe-range bound, while floats must be finite and not negative
zero. Map values reuse that guard. Weighted integers are checked for negativity
and safe magnitude before float conversion; float weights have their own finite,
nonnegative, non-negative-zero checks. Epochs delegate to the shared exact-int,
nonnegative safe-range rule.

The raw-byte path is separate from this suite's mapping round trips. Actual
`_parse_jcs_integer_token` preserves safe integer tokens as integers and observes
larger tokens as floats, allowing the canonical integer-looking spelling of a
float such as `1e20`. Request/grant raw decoders bound input, parse, validate and
require exact canonical re-encoding. Selected raw-byte tests in
[test_node_control_workload_wire.py](../../../../control-plane-kit-core/tests/test_node_control_workload_wire.py)
exercise that path; it must not be credited to this eight-test suite.

## Dependency and review limits

The dependency test reads [pyproject.toml](../../../../control-plane-kit-core/pyproject.toml)
and requires the literal `rfc8785==0.1.4` in declared dependencies, which matches
the inspected file. It does not inspect the installed distribution version or
its implementation. Actual shared canonicalization calls `rfc8785.dumps` and
normalizes that dependency's `CanonicalizationError`; this suite has no
conditional skip or fallback that substitutes another serializer.

Review depth: full test/helpers and fixture; retained full shared wire owner
and selected request/grant/scalar/map/field-guard paths; actual weighted-state,
grant codec and numeric/raw parser paths; selected adjacent fixture-consuming
tests, dependency declaration and canonical-wire contract. No whole language
owner or dependency audit is claimed. No imports, tests, canonicalizer or hash
execution, database or provider actions ran. These checks concern represented
values, not live authority or successful effects.
