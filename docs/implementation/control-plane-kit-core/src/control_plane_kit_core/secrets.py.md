Source: [control-plane-kit-core/src/control_plane_kit_core/secrets.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py).
Maintain this document alongside its source file. Recheck delivery codecs,
grant/receipt matching, resolver outcomes and actual consumer obligations when
changing the secret language.

This 1025-line module defines secret identities, delivery syntax, reference-only
grants and receipts, runtime outcome values, resolver/custodian protocols and an
explicit in-memory development resolver. Its direct package dependencies are
the private activity/run identity predicates; the remaining imports are standard
library facilities. It does not import Operations, provider clients, HTTP,
Docker, stores or process bootstrap. Calling a require_* helper does invoke the
supplied resolver, which may perform external IO; importing or constructing the
ordinary language values does not perform that resolution.

## Identities and closed vocabulary

SecretProviderId admits lowercase-leading provider names, at most 63 characters,
using lowercase letters, digits and hyphens. SecretReference parses text with
urlsplit, requires the secret scheme, provider authority and nonempty path, and
rejects query/fragment, dot/dot-dot segments, irregular slash structure and path
segments outside the 1..128-character ASCII letter/digit/dot/underscore/hyphen
grammar. The provider is validated separately. Its derived provider_id and path
are stored on the frozen value; reference_id retains the supplied text. This is
parsed-component validation, not a whole-input canonical-text round-trip check
or an aggregate path-length/segment-count budget. CredentialReference is an alias
of SecretReference, not a separate credential-bearing class.

SecretProviderEndpointReference identifies configured composition rather than a
network URL. Its lowercase-leading identifier permits lowercase letters, digits,
dot, underscore and hyphen, up to 128 characters. Its codec checks the encode
input type and requires a mapping with exactly reference_id on decode, then a
text field and valid constructor. It does not discover or contact an endpoint.

SecretUseIntent is a closed fourteen-member purpose vocabulary, including the
two Secrets bootstrap purposes. SecretCustodyStatus is ACTIVE or REVOKED;
SecretFileMode currently contains only OWNER_READ_ONLY (0400). Error codes are
MALFORMED_REFERENCE, MISSING, DENIED and INVALID_RESOLVER_RESULT. These enums name
contracts and outcomes; they do not themselves enforce provider policy.

## Grant and receipt products

All three grant records carry workspace, provider registration, endpoint and
credential references, target reference, actor, correlation and fingerprint.
Resolution additionally carries authorization/reference-registration IDs and
intent. Custody carries custody ID and intent. Version revocation carries
revocation ID, version ID and version number. Optional operation/session/run/
activity/effect identities carry correlation; resolution also has optional probe
identity. No constructor reads the records these identifiers name.

Grant identifier fields use a 1..200-character ASCII letter/digit-leading grammar
allowing dot, underscore, colon and hyphen afterward. Fingerprints require 64
lowercase hex characters, without recomputation. Endpoint, reference and intent
fields require the corresponding typed values. Version numbers require exact
int type and a positive value. Optional identifiers are checked when supplied.
The imported [activity identity](../../../../../control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py)
and [run identity](../../../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py)
predicates require exact str type; the general grant identifier helper uses
isinstance(str). No signature, expiry, workspace authorization, provider activity
or durable-commit proof is established by constructing a grant.

The matching transformations are deliberately smaller than authorization:

| Value/predicate | Fields compared |
| --- | --- |
| SecretResolutionGrant.permits | Reference equality and intent enum identity |
| SecretCustodyGrant.permits | Reference equality and intent enum identity |
| SecretVersionRevocationGrant.permits | Reference, version ID and version number equality |
| SecretCustodyReceipt.matches | Grant type, custody ID, provider registration and reference |
| SecretVersionRevocationReceipt.matches | Grant type, revocation ID, provider registration, reference, version ID and number |

Custody receipts default to ACTIVE but accept either typed custody status.
Their matches predicate does not inspect status or compare a provider version
with the grant, which has no version fields. Revocation receipts require REVOKED
at construction and include the exact version identity in matches. Both validate
identifier/reference material and a positive exact-int version number.
The permits methods compare supplied arguments without separately validating
their types; they are local equality predicates, not complete admission gates.

Descriptors flatten endpoint/reference identities and enum values and retain
actor, workspace, correlation, fingerprint and optional IDs. They contain no
SecretValue field, but they do expose reference/identity metadata. There are no
grant/receipt decoders in this module. A descriptor or its fingerprint does not
prove that an approved operation was committed or a provider action completed.

## Delivery language and interpretation

SecretDelivery is a union of three frozen product forms:

| Form | Meaning represented |
| --- | --- |
| SecretEnvironmentDelivery | Resolve a reference for an explicit intent and inject its value under an environment name |
| SecretReferenceEnvironmentDelivery | Inject the opaque reference identity itself, without resolving a value or declaring a use intent |
| SecretFileDelivery | Resolve for an intent and deliver to a protected target with file mode and optional environment path binding |

Environment slots, including SecretFilePathBinding, use a 1..128-character
uppercase-letter-leading grammar with uppercase letters, digits and underscore.
File targets must start with /run/secrets/, equal their PurePosixPath rendering,
have no trailing slash or dot/dot-dot component, and use the reference-segment
grammar below that namespace. These checks do not mount files, enforce filesystem
permissions, inspect symlinks or bound aggregate path size. Delivery construction
requires typed references/intents/mode/path binding as applicable. Duplicate
targets and cross-source name conflicts are composition questions outside an
individual delivery constructor.

secret_delivery_sort_key pattern-matches each form into a six-string tuple:
kind, destination, reference, intent, mode and path-binding name, with empty
strings for absent axes. It provides deterministic ordering across the three
forms, not deduplication. There is no catch-all rejection branch for a caller
that violates the annotated union.

secret_delivery_from_descriptor dispatches on kind and requires exact keys for
each form, including path_binding for file descriptors. Nested path binding is
None or an exact one-field mapping. Text fields and enum constructors feed the
normal value validators. The outer value.get("kind") occurs before the try
block: this function expects a Mapping rather than validating arbitrary raw
inputs itself. Known SecretResolutionErrors are re-raised; other TypeError/
ValueError failures become a generic malformed-delivery error with the original
exception retained as cause. It is not a raw JSON parser or total diagnostic
redaction boundary.

## Runtime values and resolver protocols

SecretValue stores nonempty text, exposes it through reveal and gives a fixed
redacted repr. It does not cap length, exclude NUL, encrypt/zero memory or enforce
who may call reveal. Its generated value equality still compares the underlying
field. SecretResolved suppresses its value field in repr; SecretMissing and
SecretDenied carry the reference. These outcome dataclasses have annotations but
no constructor type checks. Reference identities remain visible in normal repr.

SecretProviderAuthority requires a typed provider and a nonempty collection of
tuple prefixes whose components match the reference-segment regex. The default
((),) authorizes every path under that provider. permits checks provider equality
and segment-prefix equality; it does not consult intent, actor, registration or
time. The constructor does not require/copy/freeze the outer prefix collection
as a tuple, and malformed non-string components can raise the regex's TypeError.
The frozen dataclass declaration is not deep immutability for arbitrary supplied
collections. Prefix grammar alone also does not establish that a prefix names
an existing or reachable secret.

SecretResolver exposes bootstrap authority and resolve(reference).
AuthorizedSecretResolver instead exposes resolve(grant). SecretCustodian declares
store(custody_grant, SecretValue) -> CustodyReceipt and revoke(custody_grant) ->
None. That revoke signature is not the separate exact-version revocation language:
Core defines version grant/receipt values, but this protocol has no revoke_version
method. Protocol annotations neither instantiate a provider nor verify that an
implementation honors its declared contract.

LocalDevelopmentSecretResolver copies supplied reference-to-text material,
checks each reference against its supplied authority and each value for nonempty
text, and wraps the copied mapping in MappingProxyType. It does not separately
type-check the authority field. resolve requires a SecretReference, returns
DENIED outside authority before looking up material, MISSING inside authority
without a configured value, and RESOLVED with SecretValue otherwise. Its repr
shows authority metadata and a redacted values marker. There is no persistence,
provider discovery, rotation, version tracking or concurrent external refresh.

require_resolved_secret calls resolve(reference); require_authorized_secret
first requires a SecretResolutionGrant and then calls resolve(grant). Both accept
a SecretResolved only when its reference equals the requested reference, return
its value, map missing/denied outcomes to fixed local errors and reject other
outcomes. Missing/denied branches do not compare their embedded reference. Neither
helper revalidates SecretResolved.value as SecretValue; that remains a resolver
contract obligation because the outcome constructor does not enforce it. They
do not catch exceptions thrown directly by a resolver. The authorized helper's
type check is not a lookup of committed authorization or proof of provenance.

## Actual composition boundaries

The [graph owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py)
requires a tuple of typed deliveries, rejects duplicate complete delivery values
and checks environment-name uniqueness across public/socket and secret forms,
including file path bindings. The graph codec supplies mappings to the delivery
decoder and wraps its SecretResolutionError. Product contracts sort/check typed
deliveries; the runtime-effect request sorts grants, rejects duplicate reference/
intent uses and checks workspace and any supplied effect ID against the request.
Those composition rules are not performed by a standalone secret constructor.

In [Operations secret-provider services](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py),
authorize_resolution checks caller scope and time, enters a unit of work,
locks correlation and active reference/provider records, checks admitted intent,
reuses matching correlation or records an authorization, projects a grant and
commits before returning it. The in-unit-of-work helper does not commit itself;
the grant projection alone only checks typed evidence and provider identity
agreement. Custody grant construction checks scope/provider admission and derives
identities; generated-reference admission checks a matching receipt before
building an Operations candidate. This is selected source composition evidence,
not an executed transaction or full review of those services/stores.

The selected external interpreter checkout at
d36788e24dcf0c43f49c398d9e01ab02ad453f02 contains
[delivery interpreters](https://github.com/OpenJ92/control-plane-kit-interpreters/blob/d36788e24dcf0c43f49c398d9e01ab02ad453f02/src/control_plane_kit_interpreters/secrets.py)
that turn these forms into environment/file material. The authorized path selects
exactly one reference/intent grant for value-bearing deliveries; identity-only
delivery forwards reference text. The bootstrap path requires a configured
resolver for any nonempty delivery tuple, including identity-only deliveries.
The selected [provider resolver](https://github.com/OpenJ92/control-plane-kit-interpreters/blob/d36788e24dcf0c43f49c398d9e01ab02ad453f02/src/control_plane_kit_interpreters/secret_provider/resolver.py)
composes endpoint/credential configuration with a client call and translates
missing/denied/revoked client outcomes; the [custodian](https://github.com/OpenJ92/control-plane-kit-interpreters/blob/d36788e24dcf0c43f49c398d9e01ab02ad453f02/src/control_plane_kit_interpreters/secret_provider/custody.py)
implements both grant-based custody revoke and a separate revoke_version method.
These are external consumer coordinates, not proof that this Core baseline was
installed there, an exhaustive client review or a successful provider action.

## Tests, security and maintenance

The full 515-line [environment/secrets suite](../../../../../control-plane-kit-core/tests/test_environment_secrets.py)
contains eighteen tests across public binding, secret contract and graph delivery
groups, plus construction fixtures. It checks selected malformed references,
resolved/missing/denied local outcomes, explicit reveal and repr exclusion with
supplied secret text, denied/missing error disclosure, bootstrap authority,
three delivery round trips, exact bootstrap file descriptors, selected path/
intent/descriptor negatives and graph/diff behavior. It does not prove arbitrary
resolver conformance, memory secrecy, protected mounting or remote policy.
The full 272-line [provider contract suite](../../../../../control-plane-kit-core/tests/test_secret_provider_contract.py)
checks ordered intents, endpoint mapping, selected matching, benign grant/receipt
descriptors, permission strings and catalogue subsets. Its
[companion](../../tests/test_secret_provider_contract.py.md) records the exact
negative-case limits. No test in those two suites invokes require_authorized_secret
or establishes a durable authorization/provider transaction.

Security: this note changes no runtime, network, auth, secret or mutation surface.
SecretResolutionError stores its supplied message without a size/redaction
validator; fixed local messages and selected redacted repr implementations do not
guarantee safe arbitrary exception text, cause chains or external resolver errors.
Reference-only metadata still needs a deliberate disclosure policy. Durable
authorization, idempotency, compensation, provider version truth and verification
remain with the owning Operations/interpreter/provider boundaries.

Authoring read all 1025 source lines, both imported identity helpers, both full
test suites and selected actual Core/Operations/interpreter consumer bodies.
Only this owner gains coverage; selected external consumers are not whole-owner
reviews. Source/test consistency, documentation links and whitespace were checked.
No application imports, executable tests, database/provider calls, source edits
or merge were performed during authoring.
