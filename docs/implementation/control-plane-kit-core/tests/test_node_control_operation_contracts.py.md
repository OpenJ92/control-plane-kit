Source: [control-plane-kit-core/tests/test_node_control_operation_contracts.py](../../../../control-plane-kit-core/tests/test_node_control_operation_contracts.py).
Maintain this document alongside its source file. When operation pairing, variable codecs, descriptor shapes, assertions or evidence limits change, verify and update this companion in the same change.

This 300-line suite has five tests for operation-local codec contracts. Helpers
require `ControlPlaneVariableOperationContract` to exist, construct an ordered
read/apply tuple and place it in a variable descriptor with a nominal VARIABLE
reference. A supplied `None` selects the helper's default tuple; other values,
including an empty tuple, are passed through for negative tests. There is no
fallback implementation or conditional skip.

## The represented contract

Actual [node_control.py](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
owns three layers:

- `ControlPlaneVariableOperationContract` requires closed operation/codec
  values. Read has no command codec and returns state; apply has a command codec
  and returns a transition result.
- `ControlPlaneVariableDescriptor` requires a tuple containing exactly read
  then apply, and checks its state/apply codecs against the variable kind.
  `contract_for` accepts a closed operation value and returns its contract.
- `ControlPlaneVariableDescriptorCodec` reads the exact outer and nested
  descriptor fields, rebuilds enum values and calls those constructors. The
  wire uses an ordered list of nested contracts rather than one flat
  command/result pair on the variable.

The first test covers the current scalar, map and weighted-routing kind/codec
triples. It compares the exact nested operation descriptors, requires the old
top-level command/result keys absent, round-trips the mapping, and compares
each lookup result with the corresponding tuple element. These are equality
assertions, not object-identity assertions. The explicit three-case table matches
the inspected current enum; it would not automatically cover an added kind.

## Negative cases

Seven local-contract cases reject a raw operation string, a command on a read,
a transition result on a read, an apply without a command, a state result on an
apply, and raw command/result codec strings. The suite does not enumerate every
invalid type or enum/codec combination.

Eight operation-index cases reject empty or partial tuples, repeated reads or
applies, reversed ordering, an extra apply and a list used where a tuple is
required. Three invalid lookup inputs—a raw operation string, `None` and an
arbitrary object—must fail. These do not separately exercise every malformed
tuple member or subclass behavior.

The kind/codec mismatch test has three cases. Scalar-with-map-command and
weighted-with-scalar-command reject wrong apply codecs. The map-kind case
instead keeps the correct map command but supplies the scalar state codec;
despite the test title, it protects the state side of the pairing too. This is
a selected matrix, not all pairwise mismatches.

Nine strict-descriptor cases reject a tuple instead of the wire list, an
incomplete or reversed list, an extra nested authority field, a missing nested
result field, unknown operation/command/result strings, and the old flat shape.
The legacy case both omits `operation_contracts` and adds obsolete outer keys;
actual key validation reports unknown fields first, so it does not independently
isolate missing outer contracts. The nested missing-field case is separate.
The value `"secret"` in an extra field tests shape rejection, not secret
recognition or error redaction. All these negatives assert the contract error
class without checking bounded text, cause/context or logging behavior.

## Consumer and evidence boundaries

Actual `NodeControlResultCodec` consumes `contract_for(operation).result_codec`
during result decode and validation, and also checks successful read state codecs
against the variable. That source read explains why operation-local pairing
matters; this five-test suite does not construct result values or exercise that
consumer. It also does not execute a command, publish a route, authenticate a
caller or demonstrate an atomic state transition.

The final assertion checks the
[Core root binding](../../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py)
is the same contract type. It does not check `__all__` membership or the whole
root export surface. Mapping equality here supplies no independent canonical
byte/hash vector, runtime behavior, graph-membership or SDK compatibility proof.

Review depth: full test/helpers, actual full operation-contract/variable owner
sections and result-codec consumer, retained full variable-descriptor codec and
selected field/enum guards, actual key sets/kind-codec table/root binding and
selected route/capability names. The large node-control module was not reviewed
in full for this packet. No imports, tests, database, credential or provider
actions ran. This documentation explains declared composition and assertion
limits without granting execution authority.
