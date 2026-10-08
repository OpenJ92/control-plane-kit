# ADR 0011: Legible Composition Principle

## Status

Exploratory design. This ADR records a direction for discussion; it does not
define an implemented API, claim that current products satisfy the model, or
create an acceptance condition for issue #225.

## Context

Control Plane Kit already represents deployable systems as values, compiles
topologies into graphs, validates composition before effects, produces
inspectable activity plans, and separates authorization from execution. These
properties make more than infrastructure-as-code possible: they can make a
composed system understandable as a contract whose realization and evidence
are inspectable.

A product such as a database cluster illustrates the distinction. Its public
surface may offer read, write, health, and schema capabilities. Its promised
behavior may require writes to reach the primary, reads to use eligible
replicas, and a replica to reach an acceptable replication state before it
receives traffic. Its realization may contain a primary, replicas, volumes,
replication links, and routing. Its evidence may include primary identity,
replication lag, observed schema version, connection health, and routing
membership.

If only the realization graph is visible, callers must reverse-engineer the
product's promises from its internal structure. If only the public surface is
visible, operators and agents cannot explain how those promises are realized
or what observations justify confidence in them. The architecture needs a way
to discuss both without confusing an exploratory model with a shipped
contract.

## Proposed Direction

Treat a product conceptually as a four-part value:

```text
P = (I, L, G, O)
```

where:

- `I` is the interface: the capabilities, inputs, outputs, requirements, and
  control surfaces the product exposes;
- `L` is the set of behavioral laws: the promises and invariants that valid
  realizations must preserve;
- `G` is the implementation graph: the explicit deployable structure chosen
  to realize the interface and laws; and
- `O` is the observation and evidence model: the bounded observations and
  verification rules used to determine what is known about those promises at
  a point in time.

The product is therefore primarily a contract with one or more graph
realizations, not merely a graph that callers happen to interpret. The graph
remains first-class and inspectable; it is not the whole meaning of the
product.

The governing design principle is:

> **Every abstraction should hide incidental complexity without hiding
> consequential behavior.**

Incidental details may include generated node names, provider-specific wiring,
or a replaceable internal layout. Consequential behavior includes public
capabilities, safety and authorization boundaries, failure and suspension
states, retained data, externally visible changes, costs, destructive effects,
and the evidence used to claim success. A product abstraction may simplify the
former but must keep the latter inspectable.

## Validated Composition

Composition is valid only when the participating interfaces and laws are
compatible. Validation should be able to reject an invalid composition before
runtime effects occur. At minimum, a future concrete form of this model would
need to make the following questions answerable:

```text
Can each requirement in I be satisfied by a compatible provider interface?
Do the combined laws in L remain satisfiable?
Does G preserve required identity, protocol, lifecycle, and data invariants?
Can O produce evidence relevant to the laws it is intended to verify?
```

This extends the existing socket and graph-validation direction without
declaring a new validator API. A structurally connected graph is not
necessarily a semantically valid product, and the existence of a probe is not
necessarily evidence for the law an operator cares about.

## Deterministic Graph Expansion

A product expression should expand into its implementation graph through an
explicit, pure, and deterministic transformation for a fixed product value and
compiler version:

```text
(I, L, product parameters, implementation choice)
  -> deterministic expansion
    -> G
      -> graph validation
        -> ValidatedGraph
```

Deterministic expansion makes the realization reviewable, diffable, cacheable,
and reproducible. It also preserves the distinction between authored intent
and generated structure. Runtime discovery and provider observations must not
silently alter the desired graph during expansion; they belong in planning,
execution, or observation at an explicitly owned boundary.

Determinism does not require every implementation to use the same graph. Two
implementations may be substitutable when they expose compatible interfaces,
preserve the required laws, and provide adequate evidence. Their graph shapes
may differ, and those differences remain inspectable.

## Explainable Authorized Transitions

Changing a product means moving between validated realizations, not mutating a
hidden object in place:

```text
P(current) -> desired P
  -> deterministic graph expansion
    -> validated graph diff
      -> inspectable plan
        -> authorization
          -> external effects
            -> observations and verification
              -> justified state advancement
```

Every consequential transition should be explainable in terms of:

- the interface or law being established, preserved, or changed;
- the graph difference proposed to realize that intent;
- the effects that require authority and the approval that grants it;
- the evidence observed after execution; and
- any uncertainty, failed law, or follow-up action that remains.

Authorization is not implied by the validity of a composition. Validation can
show that a transition is meaningful; it cannot grant permission to perform
it. Likewise, an interpreter returning successfully is not enough to prove a
behavioral law. State advances only when the required durable execution and
observation evidence justifies it.

## Relationship To Existing Architecture

This direction is consistent with, but does not supersede:

- [ADR 0001](0001-product-form-block-algebra.md), which represents deployable
  blocks as product values;
- [ADR 0006](0006-activity-history-and-operational-observability.md), which
  preserves structured operational evidence;
- [ADR 0009](0009-package-boundary-topology.md), which assigns deployment
  language, durable truth, interpreters, products, and entrypoints to explicit
  owners; and
- the effect-free pipeline `DeploymentTopology -> DeploymentGraph ->
  ValidatedGraph -> GraphDiff -> ActivityPlan` described by the current
  package language.

Issue [#225](https://github.com/OpenJ92/control-plane-kit/issues/225) established
a concrete, legible `Deploy(current, desired)` application workflow and live
router-switch proof. This ADR neither changes that issue's accepted scope nor
adds retrospective acceptance criteria. In particular, it is not a blocker for
#225, its closeout, or work that depends on it.

## Consequences If Adopted

- Product documentation and descriptors could distinguish public interface,
  behavioral laws, graph realization, and evidence instead of flattening them
  into one structure.
- Alternative implementations could be compared for substitutability without
  requiring identical internal graphs.
- Plans and operator explanations could connect graph changes to product laws
  rather than merely listing low-level effects.
- Agents could reason over explicit contracts and evidence while remaining
  bounded by the same authorization gates as other clients.
- Product and observation design would need discipline: evidence must identify
  which claim it supports and must preserve uncertainty when it cannot support
  one.

The cost is additional modeling vocabulary and the risk of premature
abstraction. Any implementation proposal must begin with concrete products and
must show that the separation improves validation, explanation, or safe
operation before adding new public types.

## Non-Goals

- This ADR does not introduce public classes named `Interface`, `Law`,
  `ImplementationGraph`, `ObservationModel`, or `Product`.
- It does not require refactoring current product descriptors or graph values.
- It does not claim formal verification or that observations can prove all
  runtime behavior.
- It does not authorize autonomous deployment, recovery, failover, cleanup, or
  compensation.
- It does not make graphs opaque; explicit graph expansion and graph diffs are
  essential to legibility.
- It does not reopen or block #225.

## Questions Before Adoption

1. Which current product is the smallest useful exemplar for separating
   interface, laws, graph expansion, and observations?
2. Which laws are executable validation rules, which are runtime verification
   rules, and which remain documentation?
3. What compatibility relation is strong enough to call two realizations
   substitutable without hiding operational differences?
4. How should evidence identify the law, graph version, run, and observation
   time it supports?
5. Which parts belong in core values and which belong in operations or product
   packages under ADR 0009?
6. What versioning rules preserve deterministic expansion as compilers and
   product implementations evolve?

A later ADR may move this direction to proposed or accepted status only after
a concrete exemplar answers these questions and names its compatibility,
security, durable-data, and migration consequences.
