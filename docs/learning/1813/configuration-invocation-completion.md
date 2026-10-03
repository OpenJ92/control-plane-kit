# C1 #1927: configuration invocation completion

Status: bounded implementation ready for source review; owning green pending.
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

## Validation and implementation checkpoint

Target-only commit `c659fb5e0a18100a9e7e1cc99ba8fec38ba1db99` passed independent
target review. The unchanged Core Docker gate exited 1: support 21 passed;
integrity 941 methods, zero mocks/skips; main suite 941 tests in 56.049 seconds,
12 intended missing-language failures and zero errors. Other 929 methods passed.
Compile/import phases did not run after expected red. Log SHA256:
`215d43cc8bc57dee9237f912cc020c8ecf33445b6a95f2d6fb3296d99fd0c95c`.
[Durable red evidence](https://github.com/OpenJ92/control-plane-kit/pull/1929#issuecomment-5969645209).
Meridian independently verified the terminal evidence and released minimal source.

The new module uses the existing full-selection codec, original intent helper,
bounded identity validation and full-result fingerprint. The correlation value
checks closed operation/kind/scope; result interpretation never rewrites the
result. The inventory records its existing Core dependencies and the README
shows the real-request/result reader. No existing application module, target,
gate, pin, durable owner or provider changed.

Owning green and final independent source review are pending. This checkpoint
is not implementation acceptance or a release of C2/D/provider activity.
