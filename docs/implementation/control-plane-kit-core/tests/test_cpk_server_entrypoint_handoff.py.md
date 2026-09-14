Source: [control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py](../../../../control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py).
Maintain this document alongside its source file. Recheck composition, material,
publication policy and evidence limits when changing the server handoff.

This 447-line suite contains thirteen tests across three test classes and seven
local construction helpers. It builds pure entrypoint, material and publication
handoff values. It does not build/publish/pull an image, start a child server,
resolve credentials, invoke HTTP/MCP or clean up resources.

## Fixture layers

_program binds every current service role; _transaction_rule declares READS
read-only, AUTHORIZATION without store participation, EXECUTION read-write with
transaction ownership/after-commit effects/worker/runtime authority, and other
roles read-write with transaction ownership. _uow combines those values.
_http_api combines the full read and command route catalogues.

_handoff builds read/command/security parity against that combined HTTP API and
one MCP value. It declares six readiness dependencies: store, runtime authority,
worker, HTTP API, MCP Streamable HTTP and observation. Repeated helper calls
create equivalent contract values; no running service singleton is established.
The actual [handoff owner](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py)
checks equality among process/program/unit-of-work/parity inputs and their fixed
ownership/composition policies.

_material_handoff adds product identity control-plane-kit/cpk-server/1, public
CPK_MODE=server, required CPK_PUBLIC_BASE_URL, two secret environment deliveries
for CPK_DATABASE_URL and CPK_RUNTIME_AUTH_TOKEN, and a JSON configuration artifact
targeting /etc/cpk/server.json with mode=server content. The secret inputs are
provider-qualified references and use-intent enums, not database URLs or token
values. The [secret delivery record](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
describes an environment destination/reference/intent; construction performs no
secret lookup. The [configuration record](../../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py)
holds content and target metadata; no file is installed by this fixture.

_publication_handoff adds a ghcr.io image reference with a synthetic digest made
of 64 `a` characters, tag 0.1.0 and linux/amd64 platform. Its
[OCI/product values](../../../../control-plane-kit-core/src/control_plane_kit_core/products.py)
describe identity and an immutable image reference, not a verified registry image.

## Entrypoint assertions

Two tests in the entrypoint class pin the external implementation package,
ONE_DEPLOYMENT_PROGRAM, PROCESS_GLOBALS_ARE_NOT_TRUTH and cpk-server-imports-core.
An identity assertion checks that handoff.http_api returns the same object as
handoff.process.http_api; it does not assert that every separately constructed
fixture input is the same object. The descriptor test fixes its kind,
round-trips the mapping, excludes fastapi/uvicorn/dockerfile/oci-image from benign
lowercase repr and rejects one extra outer key.

Two later tests, located in the material test class, check entrypoint negatives:
replacing the process API with read-only route inventory, claiming process globals
own truth, and setting the implementation package to control-plane-kit-core.
The first process also omits the original readiness-dependency declarations, but
the relevant handoff guard compares its HTTP value with the retained combined
parity inputs. This tests input agreement, not a universal assertion that every
possible server contract contains the full canonical route inventory. The
tests do not inspect real process globals or package imports for these laws.

## Publication policy assertions

Four tests check publication contracts. Positive assertions require a truthy
non-root flag, forbidden runtime installation, a least-privilege read-only root
filesystem policy, private-by-default publication with explicit public endpoints,
the exact four smoke-obligation names and retained-data-preserving cleanup policy.
The smoke tuple is http-readiness, http-read-route, mcp-tool-call and shutdown-cleanup.
These are obligations, not recorded smoke successes or proof of a container user,
filesystem, network exposure or cleanup outcome.

The publication descriptor kind and round-trip equality are checked; its image
digest must equal the synthetic `sha256:` plus 64 `a` characters. Its benign repr
excludes dockerfile, pip install and apt-get. One extra outer key is rejected.
There is no digest download/hash verification or unpinned-image negative here.

Five direct negative constructions reject non-root=false, runtime installation
allowed, an otherwise constructed image with tag latest, a one-item readiness-only
smoke tuple, and a cleanup policy that deletes retained data. The latest-tag
fixture still has a digest (64 `b` characters); it exercises the handoff's tag
restriction rather than missing digest admission. The exact positive smoke tuple
and missing-obligation negative do not test reordered or duplicated obligations;
the actual smoke helper compares sets. No destructive cleanup is attempted.

## Material declarations and selected admission

The material class's positive test pins the product key, required public/secret
environment names and configuration targets, descriptor filename,
ordinary-external-product-data admission and not-auto-registered policy. These
checks do not supply CPK_PUBLIC_BASE_URL or register the product in a live server.

The material descriptor test fixes its kind and mapping round trip, excludes
postgres://, do-not-disclose and private.endpoint from benign repr, and rejects
one extra outer key. The fixture has no actual secret values or injected
do-not-disclose canary. Round-trip equality is not an independent wire/hash oracle
or proof that arbitrary configuration/environment content is safe to disclose.

Two negatives remove one declared secret delivery or all configuration artifacts
while keeping their required-name/target lists. The actual owner checks those
requirements as subsets of delivery environment names and artifact targets.
These cases do not resolve a provider, verify a secret value or inspect an
installed file. Another candidate bakes https://private.endpoint into the public
base-URL binding; the owner rejects a case-folded substring marker. This is a
selected filter case, not comprehensive private-address or secret detection.
A final material-policy candidate rejects auto-trusted self-registration.

## Error and evidence boundaries

All negative cases assert InvalidCpkServerHandoffContract, without checking error
text, repr, size, redaction or cause/context chains. The owner uses exact-key
descriptors and constructor checks, with selected ValueErrors wrapped using their
text and cause. Three computed mapping round trips and one extra-key rejection
per handoff kind do not exhaust missing/wrong/nested fields or establish
cause-free public diagnostics. The absent-word assertions use benign metadata,
without the shared descriptor-secret helper.

The structure tested is entrypoint composition -> runtime material declarations
-> publication obligations. It preserves explicit ownership and requirements as
data. Actual authorization, custody, deployment, availability, publication and
retained-data safety still require owning implementation and live evidence.

Authoring read all 447 lines, thirteen tests and seven helpers, actual handoff
entrypoint/material/publication constructors, descriptors, factories and selected
private guards, plus relevant public product/environment/secret/configuration
definitions. Reviewed HTTP/parity contracts and selected process context were
retained; this is not a full new review of those owners or the 827-line handoff
owner. Only this test gains coverage. No source changes, application imports,
executable tests, builds, providers, live actions or merge occurred. Documentation
links, whitespace and the frozen source/test guard were checked.
