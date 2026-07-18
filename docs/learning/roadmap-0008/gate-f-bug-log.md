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

## GF-003: Docker Desktop exhausted build-cache storage

- **Issue:** #357
- **Symptom:** The complete suite stopped before test execution while BuildKit
  unpacked the test image: `no space left on device`.
- **Violated law:** Docker-first validation must be repeatable without touching
  retained or data-bearing runtime resources.
- **Root cause:** Repeated Roadmap 0008 image builds accumulated hundreds of
  reclaimable BuildKit cache layers inside Docker Desktop.
- **Classification:** Local validation-environment exhaustion; no application
  assertion or runtime behavior failed.
- **Alternatives considered:** Increase Docker Desktop's disk allocation,
  delete images or anonymous volumes, or prune only build cache. Image and
  volume deletion risked unrelated live or retained systems.
- **Chosen fix:** Inspect with `docker system df -v`, then run
  `docker builder prune --all --force`. Running containers, named volumes,
  anonymous volumes, and application data were left untouched.
- **Test integrity:** No test, assertion, fixture, timeout, or application code
  changed in response to this environment failure.
- **Validation:** Restart the complete Docker/Postgres suite from the beginning.
- **Downstream consequences:** Gate F validation may periodically need scoped
  BuildKit cleanup because every issue deliberately rebuilds in Docker.
- **Residual risk:** Docker Desktop has a finite internal disk; Gate F closeout
  should report cache pressure separately from retained application resources.

## GF-004: Host compile check encountered Docker-owned bytecode caches

- **Issue:** #358
- **Symptom:** A supplemental host `compileall` check could read source but
  could not replace root-owned files under existing `__pycache__` directories.
- **Violated law:** Host tooling must not become an undeclared prerequisite for
  this Docker-first Python repository.
- **Root cause:** Prior container runs created bytecode caches on a mounted
  source tree as container root.
- **Classification:** Supplemental host-check environment mismatch.
- **Alternatives considered:** Change ownership, delete caches, run host Python
  with elevated privileges, or rely on the canonical Docker test image.
- **Chosen fix:** Do not mutate ownership or introduce a host dependency. Use
  `./test.sh`, which builds and imports the package inside its declared Python
  3.14 environment.
- **Test integrity:** No test or application behavior changed.
- **Validation:** The complete Docker/Postgres suite must compile and pass.
- **Downstream consequences:** Gate F commands should avoid host bytecode writes
  unless cache ownership is deliberately normalized in a separate issue.
- **Residual risk:** Root-owned cache files remain local noise but do not affect
  source edits or Docker validation.

## GF-005: Transition-law test guessed flattened scenario graph fields

- **Issue:** #359
- **Symptom:** Four new subtests errored with `PlanningScenario` missing
  `current` because the fixture exposes `current_graph` and `desired_graph`.
- **Violated law:** Acceptance tests must consume the canonical typed scenario
  language rather than inventing a parallel fixture shape.
- **Root cause:** The test used shorthand names from the deployment program
  discussion instead of reading `PlanningScenario`'s established fields.
- **Classification:** Test-only API assumption; all existing Postgres scenario
  executions passed before the new subtest errors.
- **Alternatives considered:** Add compatibility properties to the scenario,
  flatten the scenario model, or correct the new consumer. Production aliases
  would broaden the model solely for a mistaken test.
- **Chosen fix:** Read `scenario.current_graph` and
  `scenario.desired_graph` directly.
- **Test integrity:** Preserve the exact four typed transition assertions. No
  skip, mock, relaxed assertion, or application change was introduced.
- **Validation:** Restart the complete Docker/Postgres suite.
- **Downstream consequences:** #361 must use the same canonical scenario fields
  when constructing `Deploy`.
- **Residual risk:** None beyond the planned scenario-runner migration.

## GF-006: Deploy resumptions were not bound to the parameterized graph pair

- **Issue:** #379, discovered during #364
- **Symptom:** `Deploy.approve()`, `execute_approved()`, `resume_execution()`,
  and `resume_recovered()` accepted nested evidence prepared by another
  `Deploy(current, desired, ...)` instance.
- **Violated law:** A parameterized deployment program owns one graph
  transition across every explicit suspension and resumption boundary.
- **Root cause:** `Deploy.__call__()` checked request transition identity, but
  later methods delegated directly because canonical nested evidence was
  assumed to be sufficient.
- **Classification:** Application composition identity defect. Durable command
  services retained their own plan, run, approval, worker, and graph guards.
- **Alternatives considered:** Add a new deployment identity field, rely only on
  store rejection, or derive the transition already carried by preparation
  evidence. A new identity would duplicate graph-pair truth; store-only failure
  would make the public parameterization misleading.
- **Chosen fix:** Pattern-match each accepted suspension/result shape, recover
  its canonical `DeploymentPlanRequest.transition`, and compare it to
  `Deploy.transition` before invoking any downstream stage.
- **Test integrity:** Adversarial tests use a real foreign graph pair and assert
  rejection before approval or run persistence. Existing successful execution
  remains unchanged; no assertion or authorization check was relaxed.
- **Validation:** Complete Docker/Postgres suite: 706 passed.
- **Downstream consequences:** API, MCP, and UI callers may safely retain a
  parameterized `Deploy` across approval and recovery pauses without it
  consuming another workspace transition's evidence.
- **Residual risk:** Durable services remain the final authority for graph IDs
  and concurrency. The application guard intentionally compares typed graph
  transitions, not mutable pointers or a new deployment identifier.
