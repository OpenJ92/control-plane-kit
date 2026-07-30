# Durable Secret Provider Threat Model

Status: Accepted for `HARDEN.SECRETS`
Last updated: 2026-07-29

## Purpose

Control Plane Kit already has the right pure language for secret use:

```text
SecretReference
  -> authorized use
    -> IO-boundary resolution
      -> SecretEnvironmentDelivery | SecretFileDelivery | specialized credential
```

The missing production capability is durable custody. Current live paths can
record durable `SecretReference` identities while raw values live in
process-local maps. That is acceptable for source-live proof and unacceptable
for restart-safe operation.

This design defines the first provider contract and package ownership for a
dedicated durable secret provider. It does not replace the core secret language.

## Package Ownership

```text
control-plane-kit-core
  SecretReference
  SecretProviderId
  SecretProviderAuthority
  SecretDelivery
  SecretResolution result vocabulary

control-plane-kit-operations
  RegisteredSecretProvider
  workspace-scoped provider admission
  secret-handle metadata
  use authorization
  audit correlation
  no ciphertext or plaintext custody

control-plane-kit-secrets
  encrypted durable secret storage
  authenticated write/resolve/revoke API
  secret version and rotation metadata
  provider-local audit records
  no graph/planning/runtime ownership

control-plane-kit-interpreters
  provider client/resolver adapters
  IO-boundary materialization into Docker, Cloudflare, OCI pull, TLS, etc.

cpk-server
  composes configured provider clients and operations services
  exposes admission/metadata workflows
  never becomes the durable secret store

control-plane-kit-servers
  publishes the secrets-server product descriptor/image later
```

The first implementation should create a sibling repository/distribution:

```text
OpenJ92/control-plane-kit-secrets
```

This is intentionally separate from operations. Secret custody has a distinct
security boundary, dependency lifecycle, encryption policy, audit surface, and
operator risk profile.

## First-Flight Master Key Custody

The minimal provider stores ciphertext in its own database. The encryption
master key must not be stored in that database.

For first flight, use a required mounted file:

```text
CPK_SECRETS_MASTER_KEY_FILE=/run/secrets/cpk-secrets/master-key
```

The file contains one bounded encoded symmetric root key. The provider reads it
at process start, derives encryption keys in memory, and never returns it
through any API, read model, event, log, or error.

Why mounted file first:

- it avoids the circular dependency of resolving the secrets provider master
  key from the same provider;
- it works in local Docker and future runtime islands;
- it is easier to audit than an environment variable;
- it can later be replaced by OS keychain, cloud KMS, or hardware-backed key
  custody without changing core `SecretReference` language.

The provider may persist only a bounded key fingerprint and key version id for
diagnostics and rotation evidence. It must not persist the raw master key.

## Provider Authentication And Secret-Use Authorization

Provider authentication and secret-use authorization are distinct.

Provider authentication answers:

```text
which cpk-server/process/client is calling this secrets provider?
```

Secret-use authorization answers:

```text
is this authenticated caller allowed to resolve this reference for this
workspace, purpose, operation, and effect?
```

Operations owns the workspace policy decision and produces auditable use
context. The provider enforces provider-local admission and records the resolve
attempt. A caller that can authenticate to the provider does not automatically
receive every secret.

The first provider client contract should require:

```text
workspace_id
SecretReference
SecretUseIntent
operation/session/run correlation
caller/provider credential
idempotency or request id
```

and return one of:

```text
SecretResolved(SecretValue)
SecretMissing
SecretDenied
SecretRevoked
SecretProviderUnavailable
```

Core currently has `SecretResolved`, `SecretMissing`, and `SecretDenied`.
Additional provider outcomes may be operations/interpreter adapter errors until
a child issue proves they belong in core.

## Secret Use Intents

The first durable provider must support closed intents. Do not introduce a
free-form "give me the string" API.

Initial intents:

```text
cloudflare.tunnel-token
cloudflare.api-token
docker.remote-tls.ca-certificate
docker.remote-tls.client-certificate
docker.remote-tls.client-key
docker.local-socket-access-marker
oci.pull-credential
postgres.password
gateway.probe-signing-key
application.control-token
```

Each intent carries enough bounded metadata to audit why the material was
resolved without including the material itself.

## Current Secret Flows To Replace Or Preserve

Replace with provider-backed custody:

- generated Cloudflare tunnel tokens recorded as `GeneratedIngressSecretReference`;
- gateway probe private signing keys;
- Docker TLS certificates and client key material;
- private OCI pull credentials;
- Postgres passwords for product/runtime checks;
- app/control tokens for future CPK-enabled servers.

Preserve as development/test-only fixtures:

- `LocalDevelopmentSecretResolver`;
- cpk-server JSON bootstrap secret maps;
- source-live generated keypairs;
- harness-created ephemeral secrets.

Preserve as core language:

- `SecretReference`;
- `SecretEnvironmentDelivery(reference, intent)`;
- `SecretReferenceEnvironmentDelivery`;
- `SecretFileDelivery(reference, intent)`;
- `SecretProviderAuthority`;
- `SecretValue(<redacted>)`.

Value-resolving delivery intent is explicit language. It must not be inferred
from an environment variable name, file path, product identity, or interpreter
kind. `SecretReferenceEnvironmentDelivery` is the non-resolving exception: it
delivers only the opaque reference identity and does not authorize or perform
secret resolution.

## Storage And Encryption Requirements

The minimal provider must use a maintained authenticated-encryption library.
Home-grown encryption is forbidden.

Provider persistence must store:

- provider-local secret id;
- workspace/scope metadata;
- version id;
- encrypted value;
- encryption algorithm identifier;
- master-key fingerprint/version;
- created/updated/revoked timestamps;
- actor/correlation metadata;
- bounded labels and purpose metadata.

Provider persistence must not store:

- plaintext secret value;
- graph descriptor material containing raw secrets;
- runtime request descriptors containing raw secrets;
- unbounded caller payloads;
- bearer credentials or compact gateway tokens.

## Audit Requirements

Every resolve attempt must be auditable, including denied and missing attempts.

Audit records should include:

- provider id;
- workspace id;
- secret reference;
- secret version;
- use intent;
- caller identity or service subject;
- operation/session/run/probe correlation where available;
- outcome;
- timestamp;
- bounded error code.

Audit records must not include plaintext, ciphertext, private keys, passwords,
tokens, Docker endpoints classified as secret, or generated tunnel tokens.

## Rotation And Revocation

Rotation creates a new version. Revocation prevents future resolution.

Consumers that need overlap, such as gateway signing keys, must model overlap
explicitly:

```text
admit new private-key reference
publish new public verification key
start signing with new key
keep old public key through maximum token lifetime
revoke old private reference
remove old public key after overlap
```

Revocation must fail closed before external mutation. A revoked OCI pull
credential must fail before image pull. A revoked Docker TLS key must fail
before Docker connection. A revoked gateway signing key must make cpk-server
not ready for delegated gateway probes.

## Transport Requirements

The provider API must be authenticated. Plain unauthenticated HTTP is not a
production option.

First local implementation may use explicit development credentials and Docker
networking for tests, but the contract must remain compatible with mTLS or
signed service tokens. Transport encryption protects bytes in motion but does
not replace provider authorization or operations-level use authorization.

## Failure Semantics

Missing, denied, revoked, malformed, unavailable, and invalid-result outcomes
must be bounded and redacted.

Interpreters must fail before external mutation when required secret material
cannot be resolved. Examples:

```text
missing Cloudflare API token -> no Cloudflare tunnel mutation
missing generated tunnel token -> no cloudflared connector start
missing OCI pull credential -> no image pull
missing Docker TLS key -> no Docker client connection
missing Postgres password -> no Postgres semantic check
missing gateway signing key -> no gateway dispatch
```

## Non-Goals For First Implementation

- cloud KMS integration;
- AWS/GCP secret manager integration;
- arbitrary plugin loading;
- frontend secret UX;
- multi-tenant hosted SaaS security claims;
- full secret-sharing workflows;
- general raw secret read endpoints;
- storing provider master keys in CPK operations tables.

## Stop Conditions For Children

Stop if any child requires:

- raw secret values in core descriptors, graph descriptors, runtime request
  descriptors, events, observations, read models, logs, HTTP/MCP responses, or
  errors;
- a free-form public raw-secret read API;
- cpk-server to become the secret store;
- operations to persist ciphertext or plaintext;
- the provider master key to be stored in the provider database;
- provider-specific SDK types in core;
- product-name branches for secret materialization;
- unaudited resolution;
- revocation that does not fail closed before external mutation.
