Source: [health_effect_attempt_start.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/health_effect_attempt_start.py).
Maintain this companion alongside its source.

`StartHealthEffectAttempt(start, context)` combines the original worker-fenced
start with Core's existing trusted workspace context. It admits exact
`ObserveNodeHealth` only. The context, principal, identity and grants are checked
nominally and reconstructed before use; a forged grant tree, foreign workspace,
nonhealth operation or actor outside the existing secret-use identifier domain
receives a bounded `InvalidOperationCommand`. Credential verification still
belongs to the process owner. The current actor is not inferred from the worker,
requester or approver, and its scopes do not replace the worker's execution scope.

`HealthEffectAttemptStartResult(start, preparation)` pairs exact `NewlyStarted`
or `ExistingAttempt` with the #1851 unsigned record. Constructor validation joins
attempt identity, request fingerprint and original event identity; an evolved
attempt can still carry its original preparation. Result validation follows the
existing `OperationsRecordError` convention. Both fields are repr-protected.

```python
command = StartHealthEffectAttempt(existing_start, trusted_context)
result = service.execute_health(command)
```

The result is committed unsigned evidence, not a freshness claim or dispatch
capability. Replay remains observation only. The target contract tests protect
exact exports/signatures, frozen values, nominal forgeries, independent scopes,
actor/workspace constraints and original-event joins. No route, credential
decoder, provider call or schema is introduced here.
