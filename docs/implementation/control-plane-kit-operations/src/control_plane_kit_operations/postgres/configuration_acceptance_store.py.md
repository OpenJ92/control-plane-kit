Source: [configuration_acceptance_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/configuration_acceptance_store.py).
Maintain this companion alongside its source.

Owns original acceptance evidence inside the existing advancement UoW. Independent
workspace-wide original action/event selectors use typed numeric revision ordering
and two candidates before any source join. Bounded point reads validate the complete
paired originals, header, destination projection and complete slot digest.

The current source adds first new-node configuration membership to zero-slot
acceptance. It enumerates all selected artifacts, verifies complete full material,
and requires direct self-birth refs/claims plus the exact advancing execution's
qualifying original intent and successful direct outcome. Existing configured
current membership, historical carry and reuse remain explicitly unsupported at
this intermediate checkpoint. Missing
initial or historical evidence refuses; no adoption, repair or synthetic header is
performed. Original replay validates its own occurrence, not the latest pointer.

All reads and scalar mutation returns share the command ledger. Before CAS,
generated reader coordinates and PostgreSQL JSON sizes are checked and a fixed
publication envelope is tested against remaining command capacity. Slot/header insertion
also verifies actual consumer readability before the caller commits CAS and both
original records. Current-schema verification keyset scans both originals and
headers, with bounded page discovery and one closed budget per receipt. It checks
the latest paired occurrence against each retained workspace pointer. Claims are
retained; no cleanup or provider effect is introduced.

Before CAS the nonempty path measures the actual owner/projection read footprint,
accounts complete ref/claim material and generated slot transport, and reserves
the original pair/header envelope. The complete cold snapshot must fit 3 MiB;
snapshot plus measured source proof and discovery allowance must fit the global
4096-record/16 MiB limits. Publication reserves the reviewed fixed owner tail plus
every slot insertion return, size/value pass and sentinel. Exact immutable PK caches
avoid retransporting source evidence during in-transaction readback. All actual
duplicate reads still count. Original intent hashing is capped at 512 distinct
parents in the shared proof reader. Maximum-budget executable proofs remain open.

The prior zero-slot, rollback, binding-lifetime and newest-selection gates passed.
Three genuine nonempty targets reached causal red before this source change.
All 23 acceptance tests, including four nonempty/regression cases, passed in the
88-test focused run. Two exact declaration failures were corrected in a separate
three-test passing run, which also completed compilation and clean import. No
88-test rerun is claimed. This is not full B2,
public forward/inverse read, carry/reuse, provider or live acceptance.

The reconstructed-owner causal test exposed missing issuance identity. The store
now registers exactly one prepared object only after full `_prepare` validation.
Successful pure pair validation may bind it once, replacing its identity. Private
mutation checks require that exact issued object plus the existing live UoW/L and
truth checks. No global registry, public token, durable row or replay mutation is
added. Closed/rolled-back physical transactions cannot reuse the lifecycle guard.
The reviewed issuance correction passes the eight-test owning acceptance class,
including the unchanged reconstructed-owner refusal target. Remaining lifecycle,
rollback, newest-stream, budget and nonempty obligations are tracked on PR #1926.
