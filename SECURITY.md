# Security

`control-plane-kit` is not a security boundary by itself.

The graph can describe control-plane routes and mutable infrastructure nodes,
but a production control plane still needs:

- authentication,
- authorization,
- audit logging,
- secret storage,
- network isolation,
- transport security,
- operator approval gates for destructive actions.

The package intentionally keeps tokens and credentials out of examples.  Runtime
interpreters should receive secrets from a secret provider rather than graph
descriptors whenever possible.
