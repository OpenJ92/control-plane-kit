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
