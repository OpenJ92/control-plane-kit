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
pair = HealthSigningAuthorityReloadService(uow_factory).execute(command)
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
preparation codec, family/key/use IDs, fingerprint-to-authorization identity,
correlation, shared actor/session and execution context. Actual provider
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
