Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_admission.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_admission.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This is a database-level test of exact overlap-child admission under an existing rotation approval. It uses [gateway_rotation_overlap_fixture.py](../../../../control-plane-kit-operations/tests/gateway_rotation_overlap_fixture.py) for seeded authored/realized graphs, keys and approved rotation truth. Setup requires the Operations Docker suite's database, installs the schema and truncates workspace tables with CASCADE. It then opens a child session, calls the overlap projection service and requests a canonical activity plan before invoking [admission.py](../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py).

The positive path asserts plan/approval/decision and base-to-overlap projection correlation, one approval request rather than a new ordinary plan approval, and matching replay. It does not execute the child, generate private keys, restart a gateway or prove that A+B verifiers are installed anywhere.

Negative groups are navigation points for the authorization boundary:

- altered child activity/plan and foreign session/workspace cannot borrow the original approval;
- changed approval action review digest or durable decision identity is denied;
- current/desired projection and desired revision drift conflicts;
- changed publication source-operation version fails provenance;
- an extra verification key or wrong rotation status fails the exact phase;
- rejected/missing decisions and missing execute scope deny admission.

Several rows deliberately alter stored records with SQL to test admission's reaction to inconsistent evidence. That fixture technique is not a production repair API or a supported operator workflow. The approved rotation is seeded; credential authentication, destructive-principal separation, actual signing/custody, grant expiry, retirement and provider cleanup are not established here. The companion for the source describes how its imported projection checks exact old/new key material; this test file focuses on overlap, not the entire rotation state machine.

Review depth: the complete local test file was read; imported fixture setup was sampled and the consequential admission/projection code checked separately. No execution or whole-fixture certification is claimed. Validation, if later authorized, uses only the existing Operations Docker-backed package suite.
