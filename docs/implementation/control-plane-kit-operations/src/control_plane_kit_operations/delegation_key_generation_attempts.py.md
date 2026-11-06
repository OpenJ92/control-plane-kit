Source: [delegation_key_generation_attempts.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_key_generation_attempts.py).

The generation-attempt owner retains immutable reference-only intent and bounded
public provider receipt, distinct from CPK admission. Full actor identity includes
issuer, subject and kind; its subject must match the existing grant projection.
Actor issuer is bounded to 512 UTF-8 bytes without normalization. Signing issuer
is the separate pre-existing grant field. Request fingerprints exclude server
timestamps but include full identity and all stable grant semantics.

Claims use monotonic fences. Initial dispatch occurs once; reconciliation is an
explicit new claim over the same immutable intent and expected fence. A losing
claim returns without retry. Fences protect durable writes, not external process
liveness. A retained receipt makes reconciliation admission-only and cannot be
discarded. A provider replay flag may differ while identity stays exactly equal;
the original observation is retained. Admitted outcomes are terminal.

Admission rechecks full original actor and both generate/register scopes, then
uses the same active UoW for the existing generation fold and attempt admission.
The fold never commits on its own. A prior receipt transaction survives key
conflict, stale-fence failure after tentative writes, late owner error and failed
physical commit. Activation, provider I/O and public routes remain separate.

Closed typed descriptors are persistence representations, not arbitrary payloads.
Exact key sets, bounds, reconstruction and grant/receipt congruence are checked.
No provider body, private key or credential bytes enter this owner. Public key
shape uses the existing Core value; this is not independent cryptographic proof.
Core #2025 owns authenticated dispatch and explicit provider reconciliation.
