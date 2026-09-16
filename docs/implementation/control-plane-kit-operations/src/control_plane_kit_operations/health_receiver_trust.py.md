Source: [health_receiver_trust.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/health_receiver_trust.py).
Maintain this companion alongside its source.

The two closed results describe what a receiver is configured to accept.
Gateway facts are workspace, gateway, runtime, issuer, transit purpose, derived
gateway audience and public keys. Workload facts instead include the complete
target and V2 surface declaration, runtime, issuer, workload purpose, derived
workload audience and public keys. Gateway configuration does not acquire a
workload target, attempt or validity interval merely because admission knows one.

Both values require exact nominal Core identities and reconstructed nested
values. Key sets contain one through sixteen Ed25519 public keys with distinct
IDs and material. Full key reconstruction checks algorithm, normalized PEM and
fingerprint. Each key scalar must be exact `str`: Core preserves a key-ID string
subclass, so reconstruction alone cannot exclude mutable subclass attributes.
The focused both-family regression was committed before this review correction
as `86cc2729`; it is post-red strengthening without earlier execution credit.
Additional valid keys permit receiver overlap during rotation;
the operation separately selects its one active signer. Repr omits configured
material and there is no new result serializer.

`HealthReceiverSelection` carries the original authored and realized graph pins,
graph side, receiver/runtime/socket, exact product reference, canonical descriptor
document and actual selected artifact. Reconstruction checks the descriptor's
content/reference and the artifact's bytes/digest, including fields whose normal
dataclass equality excludes content. Descriptor defaults are provenance for the
slot, never a replacement for selected configuration bytes.

`HealthReceiverDecoders` is a frozen composition table indexed by exact
`(ProductReference, DelegationKeyPurpose)`. Each binding names one profile and
artifact ID/path/media/mode; duplicates refuse. Core's ConfigurationArtifact
constructor validates slot spelling/path with inert content. This does not
interpret receiver configuration. Trusted application composition supplies the
decoder; commands cannot provide one and there is no dynamic plugin discovery.
The decoder must use only supplied bytes, perform no filesystem/network/database
or provider effects, and return configured facts rather than copying expected
identity fields. Profile support and interpretation of historical bytes must
remain stable. Removal or unsupported bytes cause refusal.

Empty composition is valid for nonhealth operations and historical replay;
fresh health start and authority reload fail closed. For example:

```python
decoders = HealthReceiverDecoders((gateway_binding, workload_binding))
start = EffectAttemptStartService(uow_factory, id_factory=new_id,
    health_receiver_decoders=decoders)
reload = HealthSigningAuthorityReloadService(uow_factory,
    health_receiver_decoders=decoders)
```

New pure failures use fixed `HealthReceiverTrustError` without candidate-bearing
exception context. Core's private `reference_violation` is an explicit version
coupling: it is the existing issuer/public-material law, with no standalone
public validator. Reusing it avoids a divergent validator or fabricated context.
The dependency does not introduce Servers or external effects into Operations.

The four pure target methods and eight PostgreSQL methods specify nominal family
facts, key bounds/overlap, exact composition, actual selected bytes, original
pins, refusal/replay and owner-error preservation. At this source checkpoint,
only the earlier missing-contract red is executed; implementation green is
pending. The test decoder is not evidence of real product consumption (#208).
