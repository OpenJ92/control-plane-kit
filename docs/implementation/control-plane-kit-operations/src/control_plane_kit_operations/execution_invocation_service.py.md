Source: [execution_invocation_service.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_invocation_service.py).

For an original sample plan, the bound parent exposes the exact configured
support and a reference-only route read. The existing acquisition invocation
must already be ADMITTED/PENDING before this read; the native caller validates
the result and acknowledges RETURNED before any reached-check preparation.

The child prepares both uses in one fresh transaction after checking original
acquisition and prior-check completion. It takes the existing command/lifecycle/
request/latest-run/receipt/attempt/session prefix, reevaluates all sample scopes,
checks the original active registered runtime, runs the atomic suffix, and
observes the current lease last. Output leaves only after successful physical
commit/context exit. No transaction spans resolution, signing or transport.

History independently reconstructs each check's original pair and validates the
closed ordered invocation family, including both resolution roles before a
whole-check admission. Resolution reuses the exact sample-specific command; the
final whole-check admission revalidates both original active signer/reference/
provider chains after resolution. A sibling-check use cannot acquire a permit.
Completion/inspection use exact historical registrations, including retired
keys, without treating that metadata as fresh authority. Existing denied or
ambiguous work cannot be silently resumed. Native and normally approved API
composition must supply their own acceptance evidence.
