Source: [test_provider_policy_public_administration.py](../../../../control-plane-kit-operations/tests/test_provider_policy_public_administration.py).
Maintain this companion alongside its source.

The #2062 tests prove the public provider-policy administration boundary: closed
workspace/provider route-family dispatch, policy before owner acquisition,
typed command/current-read delegation, equivalent HTTP and MCP decoding,
bounded redacted projections, and dynamic admission without workspace-context
resolution for provider-policy routes.

#2064 adds the actual `cpk_server_services` registry and
`CpkServerOperationsApplication` composition target when both administration
owners are absent. Representative workspace and provider-policy reads traverse
static and dynamic authority through HTTP- and MCP-shaped arguments. Each must
return its existing fixed family-unavailable 404 without opening a unit of work
or invoking the dynamic workspace resolver. The target does not add a server
mapping law, provider-history behavior, or a substitute authorization owner.
