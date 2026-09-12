Source: [control-plane-kit-operations/src/control_plane_kit_operations/deployment_transitions.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_transitions.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 131-line module defines a pure transition value for two validated deployment
graphs. It owns four tags and their classification rule, while Core owns graph
validation and structural diff semantics. It creates no deployment program,
activity plan, operation session, runtime effect or durable record. Deploy is a
capitalized factory function, not an execution command.

The public family is:

```python
DeploymentTransition = (
    InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment
)
```

The module's six-name __all__ exposes that union, the four variants and Deploy;
the Operations root imports those same objects. The private _TransitionForm enum
provides initial/update/teardown/no-op discriminators. This is a finite public
typing vocabulary, not a sealed Python class hierarchy or a runtime rejection
mechanism for every possible subclass.

_DeploymentTransitionValue is a frozen slots dataclass with current and desired
ValidatedGraph fields plus an init=False GraphDiff. Each public variant is also
a frozen slots dataclass and declares its expected form as a class attribute.
Post-init recomputes classification, rejects a mismatched form with ValueError
and stores the computed diff through object.__setattr__. Ordinary construction
cannot supply diff as a keyword. There is no independent transition descriptor,
fingerprint, persistence codec or mutating update method in this owner.

Deploy calls _classify to choose the variant, discards that first returned diff
and constructs the selected class. The class's post-init classifies again and
retains that computed diff. A direct variant constructor classifies once. This
keeps direct construction subject to the same graph-form law, but does not cache
the factory's first computation or guarantee a single call to diff_graphs.

_classify requires both inputs to be isinstance(ValidatedGraph), then obtains
their graphs through require_valid and calls actual diff_graphs. The selected
[ValidatedGraph owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py)
determines validity from its stored findings: no ERROR finding means valid,
including warnings-only results. require_valid returns the graph or raises
GraphValidationError; it does not rerun validate_graph. This module trusts that
admitted wrapper context and accepts subclasses rather than reconstructing exact
wrappers or checking every graph invariant anew.

The classification order is significant:

1. Empty structural diff produces NoOpDeployment.
2. Otherwise, structurally empty current and nonempty desired produce InitialDeployment.
3. Otherwise, nonempty current and structurally empty desired produce TeardownDeployment.
4. Every remaining nonempty-diff pair produces UpdateDeployment.

NoOp therefore means that the existing diff language reports no change, not that
both graphs are structurally empty or the same Python object. Two differently
named empty graphs produce Update because graph-name change makes the diff
nonempty while neither side crosses the structural empty boundary. Equality of
graph names alone does not imply NoOp if graph-owned material differs.

_structurally_empty checks the truthiness of exactly five graph collections:
nodes, edges, runtimes, public_ingresses and delegation_authorities. It ignores
name and does not validate graph references. A runtime without children still
makes a graph nonempty. Isolated ingress/authority/edge data also makes this helper
return False even if a separately performed validation would reject the graph's
missing context. In normal classification, require_valid precedes this predicate.

The actual
[diff interpreter](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py)
checks the wrappers again, records graph-name changes and compares runtime, node,
edge, ingress and delegation material. It returns an ambiguity for incompatible
block-spec codec languages. Runtime-kind changes can be unsupported structural
changes. This owner tests diff.empty and structural occupancy, not whether every
change is executable or safely approved; a transition can retain unsupported or
ambiguous changes. Later planning must interpret them rather than assuming Update
means permission to run a deployment.

The actual
[GraphDiff value](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/changes.py)
retains graph names and a tuple of structural changes; empty is simply absence
of changes and descriptor delegates to those changes. This module preserves
that existing diff instead of converting it to a second change language. Current
and desired fields retain the supplied wrapper references, with no deep copy or
snapshot. Frozen dataclasses do not recursively freeze graph mappings or make
arbitrary nested objects tamper-proof; the owner supplies no ongoing revalidation
after construction.

Errors remain in their owning vocabulary: wrong input wrapper types yield TypeError,
invalid stored findings yield GraphValidationError, wrong direct variant form
yields ValueError, and imported diff/codec failures propagate. There is no generic
exception normalizer or redactor. GraphValidationError can mention the graph name
and retain its validation result. The transition value also retains both complete
graphs; it is not itself a public redacted projection of their contents.

The
[eight-test companion](../../tests/test_deployment_transitions.py.md)
documents shared root/module identity, five representative factory forms, direct
diff derivation and rejection of a supplied diff, four wrong-form examples, invalid
current-side validation, differently named empty graphs and modern-surface updates.
Its gateway fixtures use a local pure materializer and synthetic endpoints, without
Docker execution. Its five isolated collection witnesses call the private emptiness
helper without validating those potentially incomplete graphs.

That suite's finite AST/import-root and source-text exclusions support the intended
pure boundary but do not analyze every transitive/dynamic import or effect path.
It does not exhaust every variant/pair combination, subclass/forged-wrapper case,
codec mismatch, unsupported diff or later nested mutation. These limits should
remain distinct from the actual source rules and from a future deployment-program
or interpreter's validation responsibilities.

The module has no authentication, database transaction, event/history writer,
provider invocation, network exposure or resource cleanup. TeardownDeployment is
a description of an empty-boundary transition; constructing it does not delete
anything or grant destructive-action approval. Its handoff is a graph pair plus
structural diff for later interpretation.

Read depth: the complete 131-line owner and every helper/export were refreshed,
with the full 397-line suite and compiler retained from the preceding slice.
Selected actual root exports, ValidatedGraph/require_valid, graph collections,
GraphDiff, diff entry and runtime/ingress/delegation comparisons were checked.
No full validator/diff/codec/algebra review is claimed. Validation was documentation-
only: local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
