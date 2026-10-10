# Shared receiver-management route language — #1963

Base: `a588355dfee19c8f60f81be3669d2d814a01bf11`; parent #1962.
This child changes only the pure language. #1964 owns installation receipts;
Servers #245 removes its duplicate route semantics during adoption.

## Governing laws and source dry run

The frozen source is Servers `71cbf5b095cad9852db07a99da6b26ab48d9e2cc`,
`products/cpk_local_gateway/src/control_plane_kit_servers_cpk_local_gateway/health_relay_configuration.py`,
binding/value/encoding/decoding at lines 47–166. It is unchanged at accepted
Servers `93d2c8a487c8c62e21f7a49a0cbb24ab46812af0` (source diff checked).
The sibling transit module contributes only its duplicate-safe JSON reader,
closed-object validation and depth-eight structural limit. No crypto or
transit-trust semantics are needed by the shared route language.

| Provenance | Law / observable result | Class / target owner |
| --- | --- | --- |
| Servers `HealthRelayConfigurationTests.test_configuration_roundtrip_closed_bounded_and_duplicate_target_refusal` and frozen codec | Same v2 profile/canonical bytes; sorted target IDs; unique IDs and full receiver targets; bounded, detached errors; strict malformed/unknown/duplicate JSON refusal | isomorphic / Core codec |
| Frozen binding/value constructors | Exact receiver/declaration types and revalidation; declaration v2 and matching socket; same workspace/runtime; explicit canonical HTTP hostname and port; 0..128 bindings, 131072 bytes | isomorphic / Core values |
| Frozen `_decode_json` | Exact bytes input, UTF-8, duplicate-key/nonfinite/float refusal, nesting at most eight | isomorphic / Core codec |
| #1962 consensus; actual gateway self-health consumer | Representative documents include self+X and self+X+Y; unchanged canonical bindings and ordering | strengthened example context / Core vectors; graph authorization remains Operations |
| ADR0009 / #1963 | Pure exports/inventory, no Servers/Operations/framework/process dependency | new boundary law / existing package architecture tests |

Source transformation: move the meaning of the binding and document into
`control_plane_kit_core.receiver_management_routes`; reuse receiver target and
declaration codecs through direct module imports. Keep the existing JSON
serialization rather than replacing it with a different canonicalization law.
Public interface: `ReceiverManagementRouteBinding`, `ReceiverManagementRoutes`,
`ReceiverManagementRoutesCodec` (`encode`, `decode`, `encode_bytes`,
`decode_bytes`), and `ReceiverManagementRoutesError`. Values retain descriptor
projection for downstream artifact construction. Encoding revalidates nominal
values. The categorical error text remains the existing wire owner's text.

Frozen byte vectors are explicit canonical documents, with source provenance;
they contain full gateway self/X/Y receiver and declaration values. They are
not a copied parser or a claim that the old Servers suite ran in this child.
The later Servers adoption owns real consumer comparison at its selected tuple.

Do not move product artifact/path constants, runtime-contract-to-hostname
selection, filesystem/environment policy, source-runtime composition or trust
matching. Do not enforce graph membership, mandatory self routes or a complete
expected route set in the codec: an empty document remains representable, and
Operations owns graph-derived eligibility. A canonical origin is syntax, not
permission to contact it. Existing planner/execution guards remain unchanged.

No further decomposition is needed for this one value/codec boundary. Byte
incompatibility would require an explicit design decision rather than repair
by a hidden compatibility mode.

## Validation and handoff

The Core owning script has no supported focused selector. North approved one
unchanged owning causal-red run with focused new tests, followed by source
review/corrections and coordinated final green; no alternate harness or gate
change. Red/green coordinates and terminal evidence will be recorded here.

Target checkpoint `161ad018a1b256d1bfe4ff99d882f9807e5029c3` received
[Kepler's independent target/interface PASS](https://github.com/OpenJ92/control-plane-kit/issues/1963#issuecomment-6100587389).
The unchanged owning Core run terminated exit 1 after **958 tests / 88.848s,
exactly eight missing-contract assertion failures and zero errors**. All eight
new methods reached the explicit missing-module assertion; the other 950 tests
passed. Support: 21 passed; integrity: 958 tests, zero mocks, zero approved skips.
Compilation/import stages were not reached after the expected red. Log SHA256:
`b78a55e92b9c6bd811da83b0981b1432a43244cdc8bb3f424cb8d00a5125b237`.
Source implementation began only after this terminal result. Full green remains
pending independent source review and the coordinated owning run.

Security/data/history: no new I/O, route, authority, secret, persistence or
provider effect. Bounded failures omit origins and candidate material. Valid
configuration does not prove installation, current authority or health.
