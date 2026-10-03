Source: [configuration_acceptance_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/configuration_acceptance_store.py).
Maintain this companion alongside its source.

Owns original acceptance evidence inside the existing advancement UoW. Independent
workspace-wide original action/event selectors use typed numeric revision ordering
and two candidates before any source join. Bounded point reads validate the complete
paired originals, header, destination projection and explicit empty slot digest.

This checkpoint supports only validated runtime-only zero-slot material. Nodes
refuse until complete membership/source-outcome validation is implemented. Missing
initial or historical evidence refuses; no adoption, repair or synthetic header is
performed. Original replay validates its own occurrence, not the latest pointer.

All reads and scalar mutation returns share the command ledger. Before CAS,
generated reader coordinates and PostgreSQL JSON sizes are checked and a fixed
publication envelope is tested against remaining command capacity. Header insertion
also verifies actual consumer readability before the caller commits CAS and both
original records. Current-schema verification keyset scans both originals and
headers, with bounded page discovery and one closed budget per receipt. It checks
the latest paired occurrence against each retained workspace pointer. Claims are
retained; no cleanup or provider effect is introduced.

Independent source review passed this bounded checkpoint for focused Docker
validation. The future envelope covers four guard rechecks, three projection
lookups, CAS/get and three writes, header/empty-slot/original pair reads, and one
run/request/plan/session plus desired graph/projection read. Graph metadata has a
separate 1 MiB cap alongside descriptor, projection and plan. Reevaluate the
envelope when this tail grows; nonempty admission and maximum-budget executable
proof are still pending. Focused Docker validation has not run yet.

The reconstructed-owner causal test exposed missing issuance identity. The store
now registers exactly one prepared object only after full `_prepare` validation.
Successful pure pair validation may bind it once, replacing its identity. Private
mutation checks require that exact issued object plus the existing live UoW/L and
truth checks. No global registry, public token, durable row or replay mutation is
added. Closed/rolled-back physical transactions cannot reuse the lifecycle guard.
The corresponding source correction is pending independent review and validation.
