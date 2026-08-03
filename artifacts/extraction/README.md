# Extraction Evidence

This directory contains bounded machine-readable evidence for the external
server-product extraction. It must not contain raw test logs, environment
variables, credentials, secret values, or unbounded command output.

`reference-baseline.json` is produced by `./reference-test.sh` from the
immutable `pre-server-product-extraction-2026-07-20` tag. The runner archives
that tag into a temporary directory, executes the archived checkout's own
Docker/Postgres suite, compiles its Python sources, records immutable inputs,
and compares Docker resource inventories before and after execution.

The runner never performs global Docker cleanup. The frozen suite creates an
anonymous Postgres volume; the outer runner removes only a volume absent from
the before-snapshot, present after the run, and detached from every container.
The exact removed identity and final residue are part of the evidence.

`reference-tests.json` inventories every unittest collection occurrence from the
same frozen tag and maps it to a stable semantic law identity. Repeated
collection of one canonical reference is represented by
`collection_occurrences`; it is not mislabeled as several independent laws.
`law-overrides.json` contains the small reviewed set of behavior qualifiers
needed when distinct products use the same test method name.

`reference-law-ownership.json` assigns every semantic law exactly one future
owner. `ownership-rules.json` names all Hello, system, and deferred-product
modules. Core is the only default, and a product-vocabulary guard prevents an
unlisted product module from falling through into core.

`reference-demos.json` groups every frozen executable script and live fixture
under a closed observable contract. Incidental normalization is enumerated;
HTTP semantics, graph effects, observations, retention, and cleanup remain
observable rather than being normalized away.

`parity-manifest.json` is the closed migration ledger generated from the law
ownership and demo inventories. It maps all 1,107 frozen references without
claiming that migration has begun: 880 entries are required and 227 deferred,
while successor and reviewed-supersession evidence are both empty. Future
milestones may add passing successor evidence or an explicitly reviewed
supersession, but may not silently remove a frozen reference.

`successor-evidence.json` is the closed index of immutable successor proof. It
is intentionally empty before migration begins. `parity-validation-report.json`
is the deterministic foundation-policy report produced by
`./validate-parity.sh foundation`; it proves complete mapping while reporting
that required migration work remains incomplete.

`semantic-test-migration-rules.json` assigns every module in the mutable legacy
test tree to exactly one review issue. These assignments are provisional review
queues, not claims of semantic equivalence. `semantic-test-migration-inventory.json`
is generated from exact committed snapshots by
`./build-semantic-test-migration-inventory.sh`. It joins all immutable reference
laws to source locations, negative-case hints, structural legacy imports,
recorded parity evidence, and name-based current successor candidates. It also
inventories mutable-only tests, helpers, legacy shell entry points, and current
package tests/scripts across core, operations, interpreters, servers, and
secrets. `./validate-semantic-test-migration-inventory.sh` fails when that
evidence drifts from the declared source commits. Candidate matches are review
inputs only; package migration issues decide actual successors, supersessions,
future owners, and archival.

`semantic-test-reconciliation.json` is the reviewed disposition overlay for the
immutable laws selected by the migration inventory. It extends the parity
artifacts; it is not a second count-based parity ledger. Each review names exact
current tests, a reviewed non-current disposition, or one open issue that owns
the still-desired law and its negative cases.

`semantic-test-reconciliation-decisions.json` contains the small explicit input
needed to regenerate completed issue slices. The builder resolves uniquely
renamed methods but fails closed when a semantic rename, future owner, or
current test identity is ambiguous. Validate a completed slice with:

```bash
python3 -m extraction_parity.reconciliation_builder --issue 1320 --check
```

The #1320 passing package evidence is recorded in
`harden-tests-parity-1320-evidence.json`; its canonical digest is indexed by
`successor-evidence.json`.

The #1321 passing operations evidence is recorded in
`harden-tests-parity-1321-evidence.json`. Its canonical digest is indexed by
`successor-evidence.json`, and its decision slice deliberately scans both live
core and operations tests because pure execution contracts and durable
application behavior have different package owners.

`semantic-migration-closeout.json` is the #1326 deletion-gate input. It binds
the current manifest, reconciliation, source inventory, successor evidence,
and #1325 aggregate by canonical digest. It reviews all demo identities, records
the exact law set assigned to each open future issue, inventories every current
package/live shell entry point, and adds source-digest-bound parity test
identities that do not belong to a distributable package.

`semantic-migration-completion-report.json` is generated with
`python3 -m extraction_parity.completion`. Zero unowned means every frozen law
is either implemented, reviewed-superseded, or assigned to one detailed open
issue. It does not claim that future-owned behavior is implemented. The older
foundation report remains useful and intentionally reports those future-owned
required laws as incomplete.
