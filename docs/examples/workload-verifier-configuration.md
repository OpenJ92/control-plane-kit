# Reading public workload verification families

A wrapper authoring client derives required families from the common workload
declaration: surface-read always, health-read when health reads are declared,
and control when variables are declared. It reads the controller's current
public verification material before constructing the common configuration.

For a receiver with health reads and no variables, the authenticated HTTP
resource is:

```http
GET /workspaces/workspace-a/workload-verifier-configuration/workload-node-control-surface-read,workload-node-health-read
```

The equivalent MCP tool is `get_workload_verifier_configuration`, with arguments:

```json
{
  "workspace_id": "workspace-a",
  "purposes": "workload-node-control-surface-read,workload-node-health-read"
}
```

Both require a principal with `delegation-key:read` in `workspace-a`. The focused
response has `workspace_id`, `kind: "workload-verifier-configuration"` and
`workload_verifier_configuration.verifiers`. Each verifier has `purpose`,
`issuer` and `public_keys`; each public key has `key_id`, `algorithm` and
`public_key_pem`. The family documents match the common Core configuration shape.
The client supplies the graph target, runtime and declaration separately.

The controller requires exactly one active issuer/key selection for each
requested family, includes that issuer's active and verify-only overlap, and
rejects missing/ambiguous authority or more than 16 keys per family. The complete
response is bounded to 64 KiB. Empty, duplicate, unsupported, excessive or
surface-read-free selectors are rejected. No private key reference or secret is
returned or resolved.

This is a consistent observation within one read transaction, not a reservation
of future signing authority. Execution still checks current authority. When
retrying an ambiguous desired-graph submission, retain its exact proposed graph
identity and generated public configuration bytes rather than selecting keys
again. The Servers client migration owns that retry behavior.

The ordinary Operations tests exercise this HTTP/MCP request mapping and real
PostgreSQL concurrency. Separately versioned Servers/SDK consumers must adopt
the new Core route coordinate before exposing it; this example is not evidence
of deployed availability or cryptographic receiver interoperability.
