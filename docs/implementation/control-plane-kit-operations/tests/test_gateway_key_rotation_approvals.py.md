Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_approvals.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_approvals.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These database tests connect [ApprovalCommandService](../../../../control-plane-kit-operations/src/control_plane_kit_operations/approvals.py) to durable [GatewayKeyRotationService](../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotations.py) truth. Setup requires the owning Docker suite's Postgres URL, installs schema and truncates test workspace state with CASCADE. It creates a workspace/session and requests a rotation with a synthetic secret reference; no secret provider, key generation, signing or gateway process is involved.

The request test checks a closed rotation subject, matching replay and descriptor omission of `secret://` and `version_id`. This is a specific safe subject/projection assertion, not proof that every internal record or exception is redacted. Another test rejects a second request identity for one rotation; changed rotation intent under the same key also conflicts.

Focused authority is intentionally separate from ordinary plan approval: `PLAN_REQUEST` cannot request rotation approval, and rotation-request or plan-execute scope cannot approve it. Default same-actor destructive approval is denied; a distinct manager with `DELEGATION_KEY_ROTATE_APPROVE` succeeds. The tests supply actors/scopes directly and do not exercise HTTP/MCP authentication.

Two linkage controls call the rotation service's advance boundary: an approval for a different rotation cannot authorize this one, and a rejected decision cannot be linked as APPROVED. These controls complement the approval owner without turning it into the rotation executor. They do not prove overlap deployment, old-grant draining, retirement, custody resolution or provider cleanup.

Review depth: full file plus the approval owner and selected current rotation/Core subject and policy contracts. Fixture IDs, timestamps and reference values are illustrative rather than live coordinates. No tests were run; the established `control-plane-kit-operations/test.sh` is the executable validation owner.
