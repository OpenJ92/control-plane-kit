Source: [health_signing_authority.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/health_signing_authority.py).
Maintain this companion alongside its source.

Before using a saved health-check permission, this service verifies that it is
still eligible for the exact deployment attempt. An expired permission or a
replacement key causes refusal; the historical preparation remains intact.

```python
command = ReloadHealthSigningAuthority(
    request_id=request_id,
    identity=first_attempt_identity,
    context=current_trusted_actor,
    authority=execution_worker,
    fence=current_lease_fence,
)
pair = HealthSigningAuthorityReloadService(uow_factory,
    health_receiver_decoders=trusted_decoders).execute(command)
# pair.preparation is the original unsigned value; the two families carry
# public verification keys and protected resolution references, not key bytes.
```

The command uses exact nominal actor/worker/fence/identity values. The current
actor needs the four accepted health/key/secret scopes; the worker separately
needs execution authority and the matching fence. A requester, approver or
worker cannot replace the saved authorization actor.

The query locks the request, request-scoped run, first attempt, latest request
run and plan. It independently validates the saved intent/original event, exact
approval request/decision and risk tuple, both pinned projections and authored
graph side. Accepted management projection derives the expected target,
runtime, V2 declaration, health kind and gateway from the approved plan. Equal
graph content cannot select a different authored side. Current workspace
lineage is not substituted for these preconvergence pins.

The shared receiver-coverage check rereads exact registered descriptor provenance
and selected configuration bytes from those original pins. Trusted product
decoders must still support the recorded profile and both receivers must cover
the retained active signer identities and configured context. Empty composition
refuses reload; it remains valid for ordinary first-start history replay, which
never decodes current receiver trust. This check adds no product-revocation
policy, schema, time observation or protected-material resolution.

Currentness is owner-local: first-start admits attempt1, while the only inverse
attempt creator locks and requires a SUCCEEDED source. Reload requires the
locked original attempt still STARTED with unchanged transition/fence and its
run RUNNING and latest for the request. A future different retry/dispatch owner
would require revisiting that proof; this service adds no such owner.

Both exact health purposes use the existing shared key-purpose locks and the
private six-row authorization/reference/provider query. The service compares
the retained key registration/material, active chain and allowed intent, exact
actor/session/run/activity/wire-attempt and original use time. The existing
pure authorization owner recomputes the complete use/fingerprint against the
actual locked reference/provider. This is value reconstruction, not a write or
new authorization. Secret resolution is not performed.

The public family constructors enforce exact nominal public-key/reference
values and their distinct health intents. Pair construction checks the original
preparation codec, family/key/use IDs, the complete existing secret-use
fingerprint, correlation, shared actor/session and execution context. The same
private fingerprint owner is used by the existing authorization producer and
this pure pair check. Coordinated changes to both actors or sessions and both
correlations cannot preserve fingerprints for the old semantics. Actual provider
capability provenance and current actor/session remain the service's locked
owner responsibility. Constructors alone do not establish current authority.
All new public values hide their fields from repr and add no public serializer;
returned SecretResolutionGrant objects remain protected capability material.

One existing database lease observation follows all locks. Its canonical time
and expiry flag must agree with the locked request. Integer floor of that
observation is supplied to both explicit Core comparison predicates; expected
context comes from current approved owners. Original shared nbf/exp remain
unchanged, with nbf inclusive and exp exclusive. A longer current lease does
not extend either saved grant. These comparisons do not authenticate a signed
credential; the service is checking protected retained unsigned evidence.

The pair is returned only after successful UoW exit. Reload writes no new event,
request, attempt, use, preparation or ID. A failed exit returns no pair and does
not imply that a driver commit failed. Store/driver and mixed existing helper
exceptions retain their identity; new pure refusal errors are bounded and
raised outside candidate-bearing caught contexts. Raw owner exceptions must
not be treated as display-safe serialized errors.

Locks end before later material delivery. The result proves eligibility at the
checked boundary, not future freshness or once-only dispatch. Interpreters #149
owns actual resolution/signing/delivery outside the database transaction; no
provider/network/schema/reset or live-resource effect is introduced here.

Validation is tracked on PR #1855. The reviewed target-only native run had20
intended missing-module failures,0errors,1706prior Operations methods passing,
and a validated committed/active fixture before each new PostgreSQL guard.
Guarded laws acquire execution credit only from implementation green. Targets
cover direct family constructors, same-purpose replacement, active chain intent
withdrawal, current attempt/approval/pins, exact original interval, independent
Core comparator refusal, unchanged history, owner/exit error identity and
concurrent read-lock retention. Injected-time tests prove orchestration and
interval boundaries; unchanged real DB clock behavior retains predecessor
owner evidence. Native implementation validation remains pending at source
checkpoint; no live/signing acceptance is inferred.

Operations1872 updates only the shared receiver owner's representation: all own-health projects the selected common Core configuration, while injected health_receiver_decoders supplies gateway transit only. Workload bindings are explicitly invalid. Exact registered/default versus actual selected slot joins and current authority coverage precede returned resolution references as before. This supersedes the earlier statement that trusted product decoders interpret both families; transaction, original pins, current actor/key/time, replay and owner-error laws are unchanged. No SDK process import or new public service parameter is added.

## O2 / #1883 current boundary

Fresh signing pairs and the retained chain query require the exact V2 preparation. This supersedes the earlier graph-bound target/current-lineage description: original authority_context and current O1 workspace selection/membership are independent required witnesses. Standalone reload first reads a nonlocking request locator, then acquires lifecycle L, request, ordered run prefix, attempt, session and workspace before current O1 permission, plan/approval, keys and the single lease observation. Nested fold must provide the private prefix from this same live UoW; bare run prefixes are refused before later locks. Reentry verifies guard transaction ownership and unchanged request/run/session/workspace, with no late L acquisition. Core V2 predicates receive the independently reconstructed workload and gateway targets. Exact history replay does not use this live authority path.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.
