# Runtime effect requests, intents and evidence

Owner: Core's
[runtime_effect_observation.py](../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py),
with Operations owning durable use of the language. Maintain this relation when
the participating contracts change, alongside their file companions.
This note describes the source selected by the [#1801 calibration](https://github.com/OpenJ92/control-plane-kit/issues/1801);
consumer versions and review coordinates belong in the adoption PR.

## The transformation and its information loss

A runtime intent is an explicit value available before a start event exists.
It retains workspace/request/run/plan/base-graph/desired-graph coordinates,
activity and operation, runtime kind, authority reference/deliveries and product
material. An executable-request value adds a generated effect identity (equal
to its source start-event identity) and transient secret-resolution grants.

Let `P(request)` be `runtime_effect_intent_for_request`, and let
`B(intent, event, grants)` be `runtime_effect_request_for_intent`.
For admitted values, the useful law is:

```text
P(B(intent, event, grants)) = intent
```

The other direction needs the omitted inputs. `B(P(request), new_event, ())`
does not recover the original request: it binds the new identity and no grants.
The constructor's use of an “intent” does not grant execution authority.

Retained material can contain private addresses and secret references.
Canonical and hashable does not mean suitable for a public report; protected
intent evidence and bounded public projections have different disclosure rules.

This separation avoids making pre-start intent identity depend on the event
created when execution begins. That purpose is stated in source docstrings and
the generated-event tests; it is not an inferred justification for automatic
recovery.

## Fingerprints and authority answer different questions

The intent fingerprint commits the canonical retained descriptor under its
versioned domain. Changing only generated event identity or transient grants
does not change it. Live results and observations use separate fingerprint
domains and different validation contracts.

Matching intent fingerprints therefore establish matching canonical intent
material for this contract, subject to the hash's usual assumptions. They do
not establish matching transient credentials, actual Docker/HTTP request bytes,
provider state, successful execution or permission to try again.

`RuntimeAuthorityReference` in
[runtime_authority.py](../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py) identifies an authority;
it does not encode its kind or prove that its registered backing authority is
still valid. An observer that needs remote Docker TLS connection grants also
requires the explicit connection-admission contract. Such admission material is
not restored by decoding the intent.

## Where the next owners begin

These are selected consumers, not an exhaustive dependency map:

- Operations [runtime_effects.py](../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
  translates pinned realization context into intent/request values.
- Operations [effect_attempt_intent_evidence.py](../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py)
  owns canonical durable intent evidence and checks reconstruction through the
  Core contract. Its validation event is a validation coordinate, not a
  dispatched attempt.
- Operations [postgres/effect_attempt_intent_store.py](../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
  owns insertion/retrieval of intent records in the caller's transaction.
  Reading that record does not recover omitted transient material.
- Operations [effect_attempt_reconciliation_interpreter.py](../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
  reconstructs with the original start-event identity and obtains fresh
  authorization for needed secret uses before constructing an observation
  request. Do not replace this owner boundary with an unapproved replay.

Core describes the language; Operations owns durable intent/attempt policy;
external interpreters own provider calls and observations. Observed absent,
indeterminate or unsupported values do not themselves authorize cleanup,
adoption or redispatch. An ambiguous external mutation remains unresolved until
the owning workflow accepts sufficient evidence.

## Reading and changing this boundary

Read the [Core companion](../implementation/control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py.md)
and its three linked test files before changing projection, reconstruction or
fingerprint semantics. They protect pure shape and identity laws; they cannot
prove live provider truth. Coordinate changed material/codec versions with the
Operations evidence owner and with external consumers at the version they
actually adopt. A pin-only adoption can change this boundary even without local
source edits.
