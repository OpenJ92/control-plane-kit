Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 138-line Operations language owns the nominal ReconcileEffectAttempt command,
the RuntimeEffectObserver protocol and four error classes. It consumes Core
attempt identity, scopes and observation contracts; it does not define a new
outcome/recovery algebra or execute reconciliation. There is no store, database
connection, observer implementation, provider call, transaction, retry or cleanup
in this file. Six names are exported and re-exported by the package root; the
service is a separate interpreter owner.

ReconcileEffectAttempt is a frozen dataclass with request_id, identity, authority
and fence. Its identity selects a run/activity/attempt; it does not carry a caller's
intent, observation, result, runtime registration or recovery decision. Request
and identity relationships to durable state are not established by construction.
The default dataclass representation exposes these coordinates and worker/scopes;
there are no repr-hidden fields or a redacted descriptor method here. Frozen
metadata is not a general defense against deliberate object-level forgery.

__post_init__ calls _valid_reconcile_command and raises the fixed
InvalidOperationCommand message effect attempt reconciliation command is invalid
when it returns False. The validator requires the exact command type, reads the
four top-level fields under an AttributeError guard, then checks nested nominal
and scalar types before reconstruction. Command subclasses, nested identity/
authority/fence subclasses, text subclasses, bool-as-int and non-tuple scopes do
not satisfy this admission path.

Text admission uses exact str, nonempty value, UTF-8 encoding length at most 512
bytes and no characters below code point 32. Invalid Unicode encoding returns
False. This is not whitespace normalization, an ASCII-only requirement or a ban
on every control-like Unicode character. Request IDs containing only ordinary
spaces pass this helper; worker IDs face the additional authority constructor's
non-whitespace check. The helper does not rewrite any submitted coordinate.

Reconstruction makes the imported contracts consequential. Core RunId and
EffectAttemptIdentity enforce their current canonical run/activity grammar:
1–200 ASCII characters, beginning alphanumeric, followed by alphanumeric or
dot/underscore/colon/hyphen. Attempt is an exact integer from 1 through
2,147,483,647. These constraints are stricter than the preliminary 512-byte text
check and remain Core-owned, not a second grammar implemented here.

ExecutionWorkerAuthority requires a nonblank worker and canonicalizes scopes by
deduplicating/sorting PolicyScope values. This validator first requires an exact
tuple whose members are exact PolicyScope, then requires the reconstructed
authority to equal the supplied one. A forged noncanonical scope tuple therefore
rejects rather than silently being repaired. Empty scopes can remain lawful value
data. ExecutionLeaseFence reconstruction requires an exact integer generation in
1..2**63-1 and its worker constraints. Worker IDs in authority and fence must be
equal. Neither the generation range nor equality says the fence is still current.

The reconstructed identity, authority and fence are compared with the originals;
the originals remain stored in the command. InvalidOperationCommand and ValueError
from those constructors become False. This is selective admission, not a blanket
exception/redaction wrapper: only the initial four field reads are covered by the
AttributeError handler, and later nested reads or unexpected failures are not
universally caught. The exact-type checks and selected hostile-input tests should
not be described as proof that every malformed object produces a categorical error.

RuntimeEffectObserver declares observe(request, authority), returning Core's
RuntimeEffectObservationResult. Its authority parameter is a supplied
RegisteredRuntimeAuthority or None; the command itself has no such parameter.
The actual registration type describes local Docker socket or remote TLS authority
and permits active or revoked values as stored data. Merely satisfying an annotation
does not prove active status, current membership, authorization or available
credentials. Those checks belong to the interpreter and relevant stores.

Core's RuntimeEffectObservationRequest admits an exact RuntimeEffectRequest,
derives its intent/fingerprint and validates its grant/connection-admission context.
The result union has six arms: succeeded, failed, absent, conflict, indeterminate
and observer unsupported. Those types preserve uncertainty rather than converting
every observation into success. The protocol's without-mutation-authority docstring
states the implementation obligation; there is no runtime protocol adapter or
provider read-only enforcement here. It is not decorated runtime_checkable.

The error base subclasses RuntimeError; NotFound, Conflict and Denied subclass
that base and carry documentation describing their categories. They implement no
message filtering, payload bound, constructor override, exception attachment or
serialization. Consumers must supply safe categorical messages. The classes do
not prevent additional Python subclasses, and constructing an error with arbitrary
text would not redact that text. InvalidOperationCommand remains a separate
workflow-command error, not a member of this reconciliation-error family.

The separately inspected [interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
revalidates commands and requires execution:operate before opening its UoW. It
checks durable request/run/attempt and current/historical authority, returning
existing fold truth for non-started state or preparing a fresh observation path.
The fresh branch leaves its initial UoW before invoking the observer, validates
returned effect-ID/fingerprint correspondence and delegates a guarded command to
the fold service. It has separate secret-use authorization and selected error
translation. This is selected consumer context, not a whole-interpreter review;
the language alone proves none of those durable or provider boundaries.

The [contract companion](../../tests/test_effect_attempt_reconciliation_contract.py.md)
records the ten governing methods: nominal/root/interface publication, 26 selected
hostile or malformed command cases, declared error/protocol shapes, twelve synthetic
observation projections, individually lawful foreign inputs, start-kind context
admission and declared inventory rows. The tests do not instantiate the service
or observer. Their safe-error checks apply to selected fixed messages and cannot
certify universal error sanitization, exact byte-boundary positives or provider
behavior. The [fixture](../../tests/runtime_effect_reconciliation_fixture.py.md)
captures optional symbols and constructs this command; it contains no observer.

Read depth: full 138-line owner and all imports as relevant contracts; actual
Core identity grammars/bounds, authority/fence reconstruction, PolicyScope,
observation request/result and registration shapes, package root exports; full
675-line contract tests and 109-line fixture; selected actual interpreter entry,
fresh observer and guarded-fold paths. No full interpreter/store review is claimed
here. No tests, application imports, databases, credentials, Docker, providers,
source or dependency changes were executed while authoring this note.
