# #1964: correlated installation receipts

Status: interface/law dry run; no implementation or executable evidence yet.
Parent #1962; accepted base `9228a51083c666f8583ceceff6494c5927bf1f0d`;
branch `codex/1964-installation-receipts` targets `roadmap/1813-runtime-control`.

## Existing computation and smallest addition

The accepted consensus is recorded on #1911, comment 6096190947. The existing
`configuration_invocation.py:82–149` derives original-intent correlation from a
real request, commits the complete canonical selection, validates the full
result, and reads optional invocation completion. Its completion permits both
succeeded and failed terminal invocations; it does not prove installation.
`runtime_effect_observation.py:504–512` revalidates the entire result and enforces
its existing 8,192-byte canonical ceiling. `configuration_instances.py:243–261`
owns selection canonical encoding, including allocation and material identity.

At accepted Interpreters I177 merge `2b8bd78fc8857b44f6b85475fa8c46c8eb2624d0`
(reviewed source `4fe9c1f`, unchanged accepted tree), Docker
`configuration.py:92–106` emits only invocation completion. The runtime's
`runtime.py:476–491` verifies the actual installed resource and selected material
before producing success; `runtime.py:324–331` strips invocation completion on
client-close uncertainty. Those are downstream producer connections for #186,
not code to move into Core. No provider execution or adoption is claimed here.

Add one adjacent pure `configuration_installation` module. Reuse
`ConfigurationInvocationCorrelation` and its public request/selection helpers;
do not duplicate context, intent hashing, selection hashing or the result
envelope. A separate reader interprets only the new reserved evidence entry.
Existing source, result fingerprints, completion and historical unprofiled
semantics remain unchanged.

## Target public interface

- `ConfigurationInstallationDisposition`: exact enum values `created`, `reused`,
  `replaced`.
- `ConfigurationInstallationReceipt`: frozen value with request/selection
  fingerprints, disposition, prior realization fingerprint (nullable), current
  realization fingerprint. Its descriptor has those five fields plus exact
  `profile="configuration-installation-receipt.v1"`. All non-null fingerprints
  are exact lower-case 64-character hexadecimal strings.
- `ConfigurationInstallationReceiptCodec`: `encode`, `decode`,
  `encode_canonical_bytes`, `decode_canonical_bytes`. Reuse RFC8785 canonical
  representation conventions from configuration instance codecs. The byte
  decoder accepts only exact canonical UTF-8 bytes, bounded at 1,024 bytes,
  rejecting duplicate keys, alternate representations and extra/missing fields.
- `configuration_installation_receipt_for_result(context, result)` reads
  `result.evidence["configuration_installation_receipt"]`, returning `None` only
  when absent after validating the complete context and result. A present value
  requires SUCCEEDED, matching outer effect ID and exact original request/full
  selection commitments. No second result envelope or implicit receipt producer.

Errors use the existing `RuntimeEffectContractError` with bounded categorical
text and detached parser context. Codec/descriptor entrypoints revalidate nominal
values. Ordinary evidence and observations are preserved byte-for-byte; the
reader never rewrites a result.

## Governing law cards and planned tests

| Class | Governing identity | Observable law / negative cases |
| --- | --- | --- |
| new-law | #1964 closed value | Six exact fields/profile; immutable detached descriptor; exact scalar/enum types; extra/missing fields and non-digests refused |
| new-law | #1962 generation table | Created iff prior absent; reused requires equal non-null prior/current; replaced requires distinct non-null prior/current; reject all other combinations |
| new-law | #1964 canonical codec | Exact mapping/byte roundtrip; canonical ordering; malformed, duplicate, noncanonical, over-bound and wrong-type byte documents refused |
| new-law | #1964 correlation | Existing real Start/Reconcile request context; exact effect/request/full-selection correlation; changed allocation/material/partial selection refused |
| new-law | #1962 successful installation | Present receipt on failed, unsupported or uncertain result refused; absent remains absent; malformed present is never absence |
| strengthened | `test_configuration_invocation` complete-result/mutated-input laws | Apply the same whole-result 8,192-byte ceiling and context/result revalidation before optional receipt extraction; nominally forged values refused |
| isomorphic | existing invocation-completion tests | Terminal completion remains distinct, supports failed terminal invocations, and neither completion-only nor bare success becomes installation |
| isomorphic | existing effect/result/selection suites | Existing request/result/selection descriptors and fingerprints remain unchanged; additive reading preserves ordinary evidence, observations and legacy absence |

Use existing real-request fixtures through module-qualified imports; do not copy
provider logic, synthesize successful durable history or import TestCase classes
into discovery. Existing accepted green at #1963 is baseline evidence, not
acceptance of new tests. Focused causal-red targets will assert only missing new
public behavior; unexpected import/fixture errors must escape. The unchanged
owning `./control-plane-kit-core/test.sh` has no focused selector and is the only
permitted Core executable gate. Do not modify it or add a replacement harness.
No decomposition is needed: this is one closed contract and its interpretation.

## Meaning and downstream responsibilities

Realization fingerprints are opaque commitments to provider-scoped immutable
resource incarnations. This syntax cannot prove provider derivation, stability,
installation, removal, provenance or truthfulness. #186 owns domain-separated
identity derivation and authoritative installed-material postconditions,
including removal for replacement and stripping on failure/uncertainty.
#1911 owns durable admission, fold, replay and managed replacement policy. Hash
inequality is neither removal proof nor authority; receipts are not signatures.
No health, allocation cleanup, recovery or external-administrator race guarantee
is introduced. Both accepted Core contracts must join at C* before adoption.

Security/data/history: bounded pure evidence syntax only; no raw secret,
configuration body or provider identifier field, I/O, dependency change, durable
mutation, migration, execution opening or historical reinterpretation. The
future trusted producer remains responsible for honest opaque commitments.

Mathematical shape: a closed disposition sum with constrained prior/current
products; canonical representation preserves the value; contextual extraction
checks existing commitments without adding authority or executing an effect.
