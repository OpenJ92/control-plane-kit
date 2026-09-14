Source: [control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py).
Maintain this document alongside its source file. Recheck composition equality,
material requirements, publication policies, descriptor admission and actual
consumer adoption when changing the server handoff.

This 827-line module owns three frozen data contracts for the external cpk-server
package: entrypoint composition, runtime material and publication obligations.
Three small factories pass supplied values to those constructors with their
canonical policy defaults. They do not start a process, resolve material, render
a product descriptor, publish an image or execute the named smoke tests.

```text
process + program + unit of work + read/command/security parity
  -> CpkServerEntrypointHandoffContract
    + product identity + public/secret/configuration declarations
      -> CpkServerMaterialHandoffContract
        + OCI image reference + publication obligations
          -> CpkServerPublicationHandoffContract
```

## Entrypoint agreement

The entrypoint record requires typed process/program/unit-of-work and all three
parity values. It fixes implementation_package to
control-plane-kit-servers/cpk-server, import_direction to cpk-server-imports-core,
composition policy to ONE_DEPLOYMENT_PROGRAM and state policy to
PROCESS_GLOBALS_ARE_NOT_TRUTH. Although the state enum also names globals-owned
truth, this constructor rejects it.

The process must carry non-None HTTP and MCP contracts. The unit-of-work program
must equal the supplied program; process HTTP/MCP must equal both read and
command parity's corresponding values; command parity must use the supplied unit
of work; security parity must use the supplied read and command parity values.
These are value-equality requirements, not object-identity or running-singleton
checks. The http_api and mcp properties return the process's stored values.

The [parity contracts](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py)
and [process contract](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/process.py)
own their own admission rules. Handoff does not independently require every
canonical route, readiness dependency or service implementation to exist. Fixed
package/import-direction strings declare ownership; they do not inspect actual
imports, process globals or a deployed package.

## Material shape and local requirements

Material requires a typed entrypoint and
[product identity](../../../../../../control-plane-kit-core/src/control_plane_kit_core/products.py)
whose namespace/name are control-plane-kit/cpk-server. Its positive revision is
governed by ProductIdentity rather than fixed to revision 1 here. Optional tuples
carry public-static environment bindings, secret-delivery values and
configuration artifacts. Four requirement tuples name public environment,
secret environment, configuration targets and product-descriptor fields.

Required environment names allow nonempty strings composed of uppercase letters,
digits and underscores; this local helper does not require an initial letter.
Required descriptor-field names additionally allow lowercase letters and dots.
Required target strings must start with slash. These helpers do not reject
duplicate values or cap lengths. The actual binding/artifact types have their
own validation; local requirement syntax is not a complete environment/path
language by itself.

Required secret names must be a subset of environment_name values advertised by
the secret deliveries. Required configuration targets must be a subset of artifact
target_path values. Extra deliveries/artifacts are permitted. A delivery without
an environment_name does not satisfy a required environment slot. Public required
environment names are declarations for runtime supply; this constructor does not
check them against the public literals or an environment provider.

The constructor checks tuple element types but does not independently reject
duplicate public names, delivery targets or artifact targets, or require disjoint
public and secret destination names. Requirement duplicates are sorted and
retained. Public bindings/artifacts are sorted by their value ordering; secret
deliveries sort by repr of their descriptors. This stabilizes local value order,
not an independently specified canonical byte encoding.

Public environment values are case-folded and rejected if they contain any of
postgres://, postgresql://, private., internal., 127.0.0.1 or 0.0.0.0. That is a
finite marker filter, not a network-address classification, DNS lookup or universal
secret detector. [Public bindings](../../../../../../control-plane-kit-core/src/control_plane_kit_core/environment.py)
also apply their own shape/bound checks. The handoff filter covers these public
environment literals, not every possible field in the nested descriptor.

Material fixes the descriptor filename to control-plane-instance.product.cpk.json,
admission policy to ordinary-external-product-data, self-registration to
not-auto-registered and runtime lookup to lookup-at-runtime. The default required
descriptor-field tuple names schema, product.identity, product.image,
product.runtime_contract, its sockets and verification. A caller-supplied field
tuple is validated for syntax, not required to equal those defaults or checked
against an actual product document. No file is emitted, registered or trusted.

The material descriptor includes public values,
[secret delivery references/intents](../../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
and [configuration artifact descriptors](../../../../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py).
Configuration descriptors contain their supplied content; the handoff does not
resolve references or replace literals with a general redacted projection. Its
typed material checks do not establish that values or files exist at runtime.

## Publication requirements

Publication requires typed material and an OciImageReference, inheriting the
image reference's digest/registry/repository/platform validation. It additionally
rejects the exact tag latest. It does not query a registry, compare the image
repository with product identity, verify a signature or inspect an image's
filesystem/user. The runtime requirements are asserted policy values:

| Field | Required value |
| --- | --- |
| runs_as_non_root | the boolean True |
| mutable_runtime_install_policy | forbidden |
| filesystem_policy | least-privilege-read-only-root |
| publication_policy | PRIVATE_BY_DEFAULT_PUBLIC_ENDPOINT_EXPLICIT |
| cleanup_evidence_policy | owned-resources-cleaned-retained-data-preserved |
| descriptor_digest_policy | descriptor-references-published-digest |

The nested process shutdown contract must declare preserve-retained-data.
Neither this comparison nor the cleanup string performs or verifies deletion,
resource ownership or retained-data preservation.

live_smoke_obligations must be a tuple of strings whose set is exactly
http-readiness, http-read-route, mcp-tool-call and shutdown-cleanup. The default
tuple has those four entries in that order, but the helper's set comparison
allows reordered or duplicated entries and does not normalize them. These are
names of required evidence, not evidence records, passed probes or executed
cleanup. A publication handoff may be constructed before such evidence exists.

## Descriptors, decoding and diagnostic limits

Each layer emits its own kind plus complete nested values and policy fields.
Entrypoint decoding reconstructs process/program/unit-of-work/parity contracts;
material decoding uses actual product, environment, secret-delivery and
configuration decoders; publication decoding reconstructs material and an OCI
reference. Public-environment decoding specifically rejects non-public-static
binding variants. Factory functions simply supply arguments and leave policy
defaults to the constructors.

Decoders require exact keys/kinds, mapping/list/text shapes and constructor
validation. String-list helpers require lists of strings; the publication bool
decoder requires exact bool. Selected ValueErrors are wrapped as
InvalidCpkServerHandoffContract with their text and cause; some checks and other
exception types remain outside that translation. Nested exact keys do not imply
universal sanitization of diagnostics or arbitrary Mapping callbacks.

There is no raw JSON parser, total descriptor byte/item/depth cap, canonical-wire
hash verifier or universal redaction boundary. Dependency-specific limits and
digests are retained where those values define them; they do not establish
aggregate limits, immutable registry evidence or safe public disclosure of every
nested field. Source-derived admission limits described here are not executed
defects or demonstrated runtime behavior.

## Consumer and test evidence

Both the Core root and operations facades expose the three records and factories.
A scoped symbol search of frozen Core/Operations source found no non-owner,
non-facade references to those names. That does not establish absence of consumers
in external server/interpreter repositories, aliases or dynamic code, nor prove
that a published server currently implements every policy. The owner itself
composes actual Core contracts; implementation adoption needs separate evidence.

The full [447-line governing suite](../../../../../../control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py)
contains thirteen tests and seven helpers. It checks entrypoint ownership and
input agreement, material requirement subsets and selected private-literal
rejection, synthetic image/publication obligations, three mapping round trips,
extra-key rejection and benign absent-word assertions. It uses no real secret
values, published image witness, HTTP/MCP call, running server or cleanup result.
The exact positive smoke tuple is stronger than the helper's set-only admission
on ordering/multiplicity; the tests do not cover every constructor freedom here.

Authoring read all 827 lines, every constructor/decoder/factory/private helper,
retained the full 447-line suite and checked actual dependency definitions,
facade bindings and scoped consumer search. Only this owner gains coverage.
No application source fix, imports, executable tests, builds, provider/live
actions or merge occurred. Documentation links, whitespace and frozen source/test
guards were checked; owning services still must establish real authorization,
custody, publication, deployment and cleanup evidence.
