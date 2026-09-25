Source: [_health_receiver_trust.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_health_receiver_trust.py).
Maintain this companion alongside its source.

One shared check joins validated approved graph pins to each receiver's selected
node, runtime and provider socket. The node's exact product reference retrieves
its registered canonical descriptor. That read establishes immutable provenance;
it does not add product-status revocation policy or a current-product lock.
Existing graph, approval, active-key and protected-provider owners retain their
current authorization responsibilities.

The trusted registry must name exactly one product/purpose binding. Its slot
must occur once in descriptor defaults and once in selected node artifacts.
Only the actual artifact is passed to the decoder. The selected profile is a
trusted parser contract; Operations does not parse product JSON. Result family,
purpose, issuer, runtime and applicable target/gateway/declaration/audience must
agree with independently projected approved intent. At least one configured
key must match the signer's full public identity; whole-keyset equality is not
required.

First-start calls this after original graph/active-key checks and before
correlation locks, time, IDs and durable writes. Reload calls it against original
pins and selected bytes before returning protected resolution references. It
does not renew intervals, create history or read a clock. Replay never enters
this helper. No new schema, material resolution, signing or external effects
are introduced.

Pure validation failures and explicit decoder `HealthReceiverTrustError` map to
the caller's fixed refusal, raised outside the caught candidate context. Store
reads remain outside those catches; unexpected decoder and owner exceptions
retain object identity. They are not safe public error payloads. The decoder
is trusted code: its no-I/O and deterministic contract requires adapter review,
not an in-process sandbox.

Targets protect selected-byte substitution with a fresh valid digest, wrong
identity/key/profile/slot, overlap, original BASE pins, repeated read-only reload,
empty composition and both decoder-free replay entrances. Existing atomicity,
rollback, uncertain-commit and concurrency assertions remain intact through
explicit fixture composition. Execution of the implementation remains pending.
