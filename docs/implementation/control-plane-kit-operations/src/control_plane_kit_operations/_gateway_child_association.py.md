Source: [_gateway_child_association.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_gateway_child_association.py).
Maintain this companion alongside its source.

Private Operations checks share original rotation approval meaning and admitted
child association. Fresh admission still owns current rotation phase, publication
version, replacement key and canonical child-plan policy.

Retained permission uses the typed immutable approval subject, recomputed review
digest and original REQUEST_APPROVAL action, exact approved decision, parent and
child workspace, original child plan/material, unique publication and exact
ADMIT_EXECUTION receipt/fingerprint. Parent and child sessions may differ; the
parent need not remain open. Expected rotation/workspace comes from independent
subject/request truth, not the receipt under examination.

No mutable rotation or deployment-checkpoint read occurs. A checkpoint is created
after admission, claim and Start and cannot grant original permission. Later
checkpoint corruption is now a rotation-workflow consistency concern; it no
longer revokes an otherwise-proven child permission. Missing/corrupt/ambiguous
original receipts still refuse. This changed rejection boundary was explicitly
released in [#1904](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5912796814).

The private publication lookup returns at most two candidates for the exact
session, action kind and desired projection. Zero or two candidates refuse;
a singleton's full independent associations are then validated. SQL does not
filter out a semantically malformed competing receipt. LIMIT bounds returned
rows and decoding count, not payload bytes or PostgreSQL scan/JSON work. There
is no full-history fallback, arbitrary session-action count cutoff or new index.

Current child pins/session, worker/fence/lease and journal eligibility remain
with their existing owners. Retry/recovery use one approval owner before and
after writes and receiver validation separately. These helpers acquire no new
lock, allocate no IDs, return no token, mutate no record and call no provider.
