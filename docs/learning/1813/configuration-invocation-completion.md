# C1 #1927: configuration invocation completion

Status: target-only checkpoint; no application implementation or validation yet.
Parent #1920; follows accepted B2 `441a06b6c300ed2d92f0c3bb9553a5f8ae3a97f7`;
blocks C2 #1928. PR destination is `roadmap/1813-runtime-control`.

The [reviewed source/interface proposal](https://github.com/OpenJ92/control-plane-kit/issues/1920#issuecomment-5969529717)
has SHA256 `6e4f889db6a86acffec63a92780901cfd31ec8c85f8b65a83afc327e175e790f`.
Meridian and Kepler passed the planning boundary. Native issue
[#1927](https://github.com/OpenJ92/control-plane-kit/issues/1927) releases the
target/red stage; source implementation follows target/red review.

## Decision and source connection

The existing canonical full-selection codec and original-intent/full-result
fingerprints already supply the necessary commitments. Add a small closed
completion/correlation language beside that shared runtime-effect contract.
No new origin certificate, fake runtime request, Operations state, provider
simulation, or cleanup authority belongs here. A legal pure context cannot prove
its original source; verified retained history and the real-request helper own
that provenance. Full result validation precedes optional profile extraction.

## Governing laws and targets

New-law cards C-N01/N02 preserve C-L01/C-L08's exact selection and original-result
laws. `tests/test_configuration_invocation.py` exercises the agreed public API:

- closed frozen profile roundtrip and invalid-field/scalar refusal;
- full-selection domain/canonical ordering and material/incarnation sensitivity;
- real request versus same-source context for StartNode and ReconcileNode;
- unsupported kind/operation/scope and malformed context refusal;
- intact ordinary result evidence, observations and failed-result details;
- absent profile versus present invalid profile;
- wrong effect/request/selection and uncertain/unsupported refusal;
- partial/altered selection refusal;
- complete result at 8192 bytes versus 8193, including absent profile;
- mutated nominal result/context/selection/request revalidation.

The target loader converts only absence of the requested new public module into
an explicit assertion. Other import errors escape. Existing real request fixtures
are reused; no skip, xfail or weaker legacy assertion is added. The unchanged
Core Docker gate has no focused filter, so its ordinary whole suite owns red and
green. No host Python, custom harness, dependency or gate change is permitted.

## Security, data and handoff

Pure bounded syntax adds no network or durable mutation. Completion is neither
provider truth nor non-use evidence; FAILED can leave attached resources. No
claim is released. C2 consumes the eventual accepted C1 contract before adding
verified inspection and exact plans/approval. D and I177 remain separate.

Validation: pending. This checkpoint is not implementation acceptance.
