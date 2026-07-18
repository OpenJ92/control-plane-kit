# Roadmap 0008 Gate F Bug Log

This append-only log records material defects discovered while composing and
proving the public deployment application. Routine implementation edits that do
not expose a violated law are not bugs and are not recorded here.

Each entry must contain:

- identifier and issue;
- symptom;
- violated law;
- root cause;
- classification;
- alternatives considered;
- chosen fix;
- test-integrity assessment;
- validation evidence;
- downstream consequences;
- residual risk.

## GF-001: Application package absent from the dependency algebra

- **Issue:** #353
- **Symptom:** The repository architecture test rejected the new
  `control_plane_kit.application` package before evaluating its actual imports.
- **Violated law:** Every top-level package is an explicit object in the package
  dependency algebra, with a closed set of permitted outgoing edges.
- **Root cause:** Gate F introduced the application-composition package without
  extending the exhaustive `PACKAGE_RULES` relation.
- **Classification:** Architecture integration defect.
- **Alternatives considered:** Place deployment composition under `workflows`,
  exempt unknown packages, or register the application boundary. The first
  obscures the distinction between atomic command services and composed
  programs; the second weakens exhaustive checking.
- **Chosen fix:** Register `application -> {topology, workflows}`. This is the
  smallest dependency surface needed by the closed values and later callable
  stages.
- **Test integrity:** The failing repository-wide assertion remains unchanged.
  A missing object was added to its expected closed relation.
- **Validation:** `test_repository_obeys_declared_dependency_and_transport_ownership`
  must pass together with the complete Docker/Postgres suite.
- **Downstream consequences:** Gate F AST hardening can narrow the deployment
  subpackage further, but cannot silently add stores, adapters, or transports.
- **Residual risk:** Later stage implementation may reveal another justified
  package edge; each edge must be reviewed and explicitly added rather than
  broadly permitting application imports.

## GF-002: Execution bounds require the canonical effect value language

- **Issue:** #356
- **Symptom:** The exhaustive dependency test rejected
  `ExecutionLimits.timeout: TimeoutPolicy` because GF-001 initially permitted
  the application package to import only topology and workflow roots.
- **Violated law:** Every application dependency must be explicit and must
  point to canonical values rather than reimplementing their semantics.
- **Root cause:** The first Gate F package rule was intentionally minimal before
  the `Execute` stage existed. Bounded execution introduces a legitimate need
  for the canonical effect timeout product.
- **Classification:** Architecture integration defect discovered by an
  executable dependency law.
- **Alternatives considered:** Duplicate timeout fields in the application
  package, hide a fixed timeout inside `Execute`, re-export `TimeoutPolicy`
  through workflows, or admit the direct canonical edge. Duplication and
  re-export obscure ownership; a fixed timeout removes required parameterization.
- **Chosen fix:** Expand the closed relation to
  `application -> {effects, topology, workflows}`. The application still may
  not import adapters, stores, SQL, transports, or effect-dispatch internals.
- **Test integrity:** The repository-wide dependency assertion remains exact.
  No import was ignored and no package wildcard was introduced.
- **Validation:** The complete Docker/Postgres suite and the dedicated Gate F
  AST policies must both pass.
- **Downstream consequences:** #360 must distinguish importing immutable effect
  contracts from calling adapters or dispatching effects directly.
- **Residual risk:** A broad import from `effects` could expose more than Gate F
  needs; #360 will constrain names and call sites inside the deployment package.
