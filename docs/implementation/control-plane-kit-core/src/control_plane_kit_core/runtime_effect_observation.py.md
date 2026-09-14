Source: [control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Runtime intent and observation language

This module owns pure pre-start intent values, the projection/rebinding between
intent and executable-request data, six provider-observation variants, and
canonical fingerprints. It does not call a provider, persist evidence, authorize
secret use, advance a graph, or decide a retry.

The [request/intent relation](../../../../architecture/runtime-effect-request-intent-boundary.md)
explains the information boundary. Locally, the important constraint is that
projection requires an exact `RuntimeEffectRequest` with matching effect and
start-event identities. Rebinding requires an explicit event identity and
explicitly supplied transient grants; omitted grants default to an empty tuple.

## Contract-bearing dependencies

- [runtime_effects.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
  owns request/result and product material contracts. This module relies on its
  operation descriptor validation and authority-recipient validation, rather
  than defining a competing runtime request language.
- [runtime_authority.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py)
  owns `RemoteDockerTlsConnectionAdmission` and connection-grant validation.
  TLS connection grants join the observer's allowed uses only after that
  validation. An authority reference is an identity, not an authority kind.
- [secrets.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
  owns exact `SecretResolutionGrant` and `SecretUseIntent` values. Observation
  construction rejects duplicate reference/use pairs, unrelated uses and
  mismatched workspace/effect/run/activity coordinates. It validates supplied
  grants; it does not obtain grants or establish grant completeness. Even
  empty/partial TLS grant tuples are structurally admitted; connection
  completeness is an interpreter obligation before resolution or provider use.
- [verification.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py)
  supplies verification outcomes admitted in live results. A successful live
  result cannot carry failed verification.
- `rfc8785` supplies canonical JSON bytes. SHA-256 uses distinct versioned
  domains for intent, live result and observation. Preserve those domains and
  canonical material when maintaining compatibility; hashing ordinary JSON
  serialization is not equivalent.

## Observation meaning and disclosure

The result sum distinguishes succeeded, failed, absent, conflict, indeterminate
and observer-unsupported. Failed/conflict/indeterminate/unsupported require a
typed failure; succeeded/absent forbid it. Absent and unsupported also forbid
endpoint observations. The other variants can carry endpoint observations.
These are representable reports, not instructions to adopt, clean up or retry.

Observation evidence must begin as a nonempty exact dictionary. Its JSON-like
contents are recursively copied into immutable mappings/tuples, with bounded
size, nesting and text. The filter rejects particular secret-shaped text,
URL-shaped strings, IPv4-shaped strings, private-key markers and raw
`address`/`endpoint` keys. It is not a universal sanitizer; producers must
select bounded non-sensitive evidence before construction.

The observation request retains transient material in its hidden request field;
its descriptor carries only effect identity, intent and intent fingerprint.
Do not infer that the descriptor reconstructs the full admitted observation
request.

Fingerprinting caps intent at 1 MiB and complete outcome descriptors at 8 KiB;
observation evidence has its own 4 KiB cap. The complete outcome cap is applied
when fingerprinting, not simply by constructing any result. Live-result JSON
validation is deliberately a different code path from the narrower observation
evidence policy: never claim the observation redaction filter protects arbitrary
live-result evidence. Result endpoint ordering participates in identity.

## Evidence limits

The three governing files separate
[intent laws](../../../../../control-plane-kit-core/tests/test_runtime_effect_intent.py),
[observation laws](../../../../../control-plane-kit-core/tests/test_runtime_effect_observation.py), and
[existing result/package boundaries](../../../../../control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py).
They cover exact nominal values, negative shapes, canonical goldens, correlation,
grant exclusions and immutable bounded observations. They do not prove that an
external observer reports truth or that Operations has durably accepted it.
Source-aware reviews of both consuming boundaries remain necessary.
