Source: [control-plane-kit-core/tests/test_secret_provider_contract.py](../../../../control-plane-kit-core/tests/test_secret_provider_contract.py).
Maintain this document alongside its source file. Recheck exact grant matching,
descriptor assertions and catalogue coverage when changing secret contracts.

This 272-line suite contains seven tests in one class and no local helpers.
It constructs reference, grant and receipt values and reads canonical contract
catalogues. No provider is contacted, secret resolved/generated/stored/revoked,
authorization record committed or HTTP request dispatched by these tests.

## Closed intents and opaque endpoint identity

The intent test fixes the exact ordered fourteen SecretUseIntent values: the
application control token; Cloudflare API and tunnel tokens; Docker local-socket
marker and three remote-TLS materials; gateway probe signing key; OCI pull
credential; PostgreSQL password; gateway transit and workload node-control signing
keys; and the two Secrets bootstrap purposes, custody-root-key and
provider-credentials-document. One unknown gateway intent raises ValueError.
The test name's secret-free claim is represented by these literal enum strings;
the test does not inject or inspect secret values.

The endpoint test fixes workspace-secrets as its identity and the exact mapping
{"reference_id": "workspace-secrets"}, then checks decode(encode(value)) equality.
It rejects an HTTPS URL, token@secrets and a descriptor with an extra url key.
The actual [secret value owner](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
admits a lowercase-leading endpoint identifier of at most 128 characters and an
exact one-key mapping. These cases do not exhaust length/type/missing-field
admission or prove that an endpoint has been configured or can be reached.

## Resolution grant assertions

The resolution fixture uses secret://provider-a/postgres/password, a separate
secret://bootstrap/provider-a-token credential reference, provider-a endpoint,
workspace/registration/actor/correlation identities, synthetic fingerprint and
run/activity/effect identities. It checks permits for the exact reference and
POSTGRES_PASSWORD intent, then independently rejects a different intent and a
different reference. The actual permits predicate compares reference equality
and enum identity only. It does not consult the actor, workspace, registration,
fingerprint, expiry, approval state or a durable store on each call.

The descriptor assertions pin endpoint and credential reference strings, exclude
the top-level secret_value key and exclude plaintext from benign lowercase repr.
For each of the two bootstrap purposes, dataclasses.replace changes the intent;
the test checks exact acceptance, wrong-reference rejection, application-token
rejection and rejection of the other bootstrap purpose. It also checks that the
new descriptor equals the original mapping with only intent replaced. This is
not a grant codec round trip or evidence of actual bootstrap secret resolution.

Construction invokes typed-reference/intent, identifier and fingerprint guards.
Those guards validate material shape; the locally constructed grant is not proof
that committed authorization exists, despite the owner's committed-authority
description. Fingerprints here are repeated literal hex characters, not an
independent computation of an approved request's digest.

## Generated custody and version revocation

The custody fixture uses a generated/cloudflare/tunnel-a reference and the
CLOUDFLARE_TUNNEL_TOKEN intent. Its receipt repeats custody ID, provider
registration and reference and supplies version-a/1. Assertions check positive
permits, positive receipt.matches and default ACTIVE status. There is no
wrong-reference/intent or receipt-mismatch negative in this test. The actual
receipt matcher compares grant type and those three repeated identities; it
does not compare receipt version or status with the grant. Receipt construction
accepts a typed custody status and a positive exact-int version number.

The revocation fixture supplies keys/gateway-a, version-a/1, an operation ID and
a matching receipt. It checks exact permits, positive matches and REVOKED status.
The rejected permits call changes both version ID and number to version-b/2;
it does not isolate either mismatch or change the reference. Replacing the grant
version number with zero raises SecretProviderContractError. The actual grant
constructor requires a positive exact int; the receipt constructor additionally
requires REVOKED. The actual matcher compares grant type, revocation ID, provider
registration, reference, version ID and number. None of its mismatch branches
is independently asserted here.

Both tests exclude secret_value from the grant descriptor and plaintext from
benign grant/receipt descriptor repr. They do not assert a full literal receipt
descriptor, a receipt codec round trip, secret-value canary removal or actual
provider custody/revocation. The receipts are directly constructed test values,
not observations returned by an external provider.

## Permission names and catalogue membership

The permission test fixes four distinct [PolicyScope values](../../../../control-plane-kit-core/src/control_plane_kit_core/policies.py):
secret-provider:register, :read, :use and :revoke. It does not create actors,
evaluate authorization or demonstrate that one permission cannot confer another.

The final test computes sets from the [command factory](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py),
[read projection factory](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/projections.py)
and [HTTP route factories](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py).
Four subset assertions require provider/reference register and revoke command
IDs, their command.* route IDs, and read.secret-providers,
read.secret-provider-detail, read.secret-references and
read.secret-reference-detail in both read catalogues. Sets and subset checks do
not pin complete inventory, order, uniqueness or agreement of schemas, roles,
authorization scopes and safety policies. The corresponding source entries are
contract data; this test does not exercise server handlers or permission checks.

## Security, evidence and maintenance limits

Selected negatives assert exception classes only, without inspecting diagnostic
text, repr, length or cause/context chains. Benign absent-key/word checks are not
a general descriptor redaction proof; there is no injected secret canary or
shared descriptor-secret assertion helper. Endpoint mappings have an explicit
literal shape oracle, whereas grant/receipt descriptors have selected assertions
and the bootstrap mapping comparison described above.

The useful structural law is that secret use is represented by explicit reference
and purpose/version values with local matching predicates. An interpreter and
its owning authorization/persistence services must supply the external effects
and evidence. Extending this suite should preserve that ownership boundary;
provider success, transaction history and policy enforcement require their own
owning tests rather than stronger claims about these pure values.

Authoring read all 272 lines and seven tests, selected secret owner definitions
through the grant/receipt section and their identifier guards, the four policy
scope members and corresponding command/projection/HTTP entries. Prior reviewed
catalogue context was retained. This does not claim a full review of the
1025-line secrets owner. Only this test companion gains coverage. Security: no
runtime, network, auth, secret or mutation behavior changed. No application
imports, executable tests, database/provider calls, live actions or merge were
performed. Documentation links, whitespace and frozen source/test consistency
were checked.
