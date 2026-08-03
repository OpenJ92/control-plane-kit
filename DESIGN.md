# Design

The current design is maintained in:

- [Control Plane Language](docs/CONTROL_PLANE_LANGUAGE.md)
- [Language Study Guide](docs/CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md)
- [Operating Model](docs/OPERATING_MODEL.md)
- [Server Product Rollout](SERVER_PRODUCT_ROLLOUT.md)

The original aggregate design centered on `DeployBlock = Spec x
RuntimeImplementation x RoleSockets`. That document described the retired
mutable package and is preserved in the immutable
`pre-server-product-extraction-2026-07-20` tag.

The current package boundary is:

```text
core
  pure topology, product, socket, policy, runtime-effect, ingress,
  authorization, verification, and secret-reference language

operations
  durable graph truth, registrations, planning, approval, lifecycle,
  coordinator, observations, and read models

interpreters
  provider-neutral requests -> concrete external IO -> bounded results

servers
  cpk-server process composition and package-owned OCI products

secrets
  encrypted durable custody and scoped resolution
```

Current validation is `./current-backend-test.sh`; immutable historical
reproduction is `./reference-test.sh`.
