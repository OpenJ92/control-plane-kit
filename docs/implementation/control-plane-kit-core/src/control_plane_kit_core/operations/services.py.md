Source: [control-plane-kit-core/src/control_plane_kit_core/operations/services.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# A service composition and its stage language

This owner names nine control-plane service roles and six ordered public stages.
Bindings associate roles with textual service names and parameter names, not
implementation callbacks. A `DeploymentProgramBoundary` requires one binding
for every role, rejects duplicates and missing roles, normalizes enum order and
supports typed role lookup. It does not construct or invoke those services.

Bindings require nonblank text and a tuple of nonblank parameter strings. The
eight-term packaging filter case-folds and replaces underscores with hyphens
before substring matching. It is a vocabulary guard, not general validation of
imports, process behavior or sensitive input. Names and parameters have no
aggregate size/redaction guarantee; rejected packaging text appears in errors.

The stage-to-role mapping is explicit:

`plan -> planning; approve -> approval; admit -> admission; claim -> lifecycle;
execute -> execution; advance -> lifecycle`.

A stage checks that mapping, the predecessor's enum type and a Boolean durable-
handoff flag. The enclosing pipeline requires exactly the six canonical stages
in order and each immediate predecessor. The canonical factory marks every
handoff true, but a custom stage with a false handoff flag can still participate
in an otherwise valid pipeline. No history record is written by these values.

The pipeline constructor substitutes the canonical sequence for any falsy
`stages` value before tuple validation; the empty default and decoded empty list
therefore select the canonical pipeline. Nonempty sequences must satisfy the
tuple, stage, order and predecessor checks. A stage alone does not establish a
complete chain or permission to execute its action.

Descriptors use enum text and lists for nested sequences. Decoders require the
specified exact key sets and mapping/list/text/Boolean shapes, then invoke the
constructors. Nested errors can retain supplied text and exception causes.
There is no canonical-byte digest, authentication or universal safe-display
boundary in this module.

Full owner and full 164-line
[governing test](../../../../../../control-plane-kit-core/tests/test_deployment_program_boundary.py)
read. The [transaction language](./transactions.py.md) consumes the program's
role set; actual orchestration, persistence and provider effects remain with
their implementation owners. This declaration does not prove a transaction,
approval, worker or durable handoff actually occurred. No executable validation
or runtime mutation was performed.
