Source: [control-plane-kit-core/src/control_plane_kit_core/identity.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Authenticated identity and workspace context

This file owns credential-free identity/grant/context values and two boundary
protocols. `CredentialVerifier` describes exchanging opaque credentials for an
authenticated principal; `PrincipalAuthorizer` describes deriving a workspace
context. There is no verifier implementation or authentication I/O here.

`WorkspaceGrant` requires a tuple of distinct closed
[PolicyScope](../../../../../control-plane-kit-core/src/control_plane_kit_core/policies.py) values and sorts them.
`AuthenticatedPrincipal` rejects duplicate workspace grants and sorts by
workspace. Its context factory fails when the workspace is absent.
`TrustedCommandContext` requires its scopes to equal the selected principal
grant, not merely be a subset; it cannot add or independently attenuate scopes.
Its durable `actor_id` is the subject ID; its descriptor also carries issuer
and principal kind.

“Authenticated” and “trusted” describe the supplying boundary's obligation.
These ordinary Python constructors do not prove credentials were verified.
Do not build principals from caller-controlled payload scopes. Similarly,
`PolicyScope` is distinct from the route-level
[HttpAuthScope](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py) vocabulary; adapters
must implement the actual mapping rather than compare unrelated strings.

The text helper checks nonblank strings, not a general length ceiling or secret
filter. Credential-free fields and fixed shape do not make arbitrary supplied
identity text safe to publish. The existing
[test_identity.py](../../../../../control-plane-kit-core/tests/test_identity.py) covers exact
descriptor expectations, denied workspace/context changes and duplicate/open
grants using constructed principals. It does not establish a working identity
provider or browser authentication path.
