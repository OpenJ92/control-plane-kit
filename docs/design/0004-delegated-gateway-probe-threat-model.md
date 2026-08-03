# Delegated Gateway Probe Threat Model

Status: Accepted for `HARDEN.AUTH.GATEWAY`
Last updated: 2026-07-29

## Purpose

`cpk-local-gateway` can reach private services that `cpk-server` cannot reach
directly. That reachability is not authority. A gateway probe is allowed only
when an authenticated operator command has been authorized by operations and
re-expressed as one exact, short-lived delegated grant.

This first capability is deliberately narrow:

```text
authenticated operator
  -> cpk-server
    -> operations authorization
      -> unsigned DelegatedGatewayProbeGrant
        -> outer signer and dispatcher
          -> gateway verifier
            -> one declared read-only target probe
```

Core defines the unsigned, provider-neutral grant and canonical request. It
does not define a compact token, signing algorithm, key store, HTTP header, or
transport.

## Assets

The protected assets are:

- operator identity and workspace grants;
- private runtime-island reachability;
- the graph-declared gateway target map;
- exact probe kind, target, and HTTP path;
- signing and verification key material;
- durable operation intent and bounded result evidence;
- tokens or signed envelopes carrying delegated authority.

No secret or compact signed envelope is durable control-plane truth.

## Trust Transitions

1. The public process authenticates opaque operator credentials into a trusted
   principal.
2. Operations authorizes that principal for one workspace and one declared
   probe target.
3. Operations commits durable intent before dispatch.
4. An injected outer signer signs the canonical grant.
5. A bounded dispatcher chooses an observed endpoint; callers do not supply a
   target URL or audience.
6. The gateway verifies the grant before target lookup or target IO.
7. Operations records only bounded results and correlation evidence after the
   external effect.

Transport encryption protects transit but does not replace authorization.
Cloudflare, Docker networking, and possession of a reachable URL do not grant
probe authority.

## Threats And Required Defenses

### Stolen Operator Credential

A stolen operator credential is limited by authentication expiry and the
principal's workspace scopes. The credential is never forwarded to the
gateway. The delegated grant has a distinct audience, exact command binding,
and a maximum five-minute lifetime.

### Forged Gateway Request

The gateway rejects missing, malformed, or untrusted grants before target
lookup or IO. Concrete signature verification belongs outside core and must use
a maintained library.

### Replay

The first gateway commands are idempotent read-only probes. The gateway keeps
bounded in-process `jti` evidence and permits one concurrent use. This does not
claim replay protection across gateway restart. Durable replay protection is a
future requirement before any mutating gateway command can exist.

### Wrong Gateway Or Runtime Island

The grant binds both the exact gateway node identity and a runtime-island
audience chosen from admitted operational truth. Neither value is caller
authored at dispatch time.

### Wrong Workspace Or Operation

The grant binds workspace, originating operation, and request correlation.
Operations derives them from trusted command context and durable intent.

### Target, Kind, Or Path Substitution

The grant binds the closed probe kind, `GatewayTargetId`, and canonical digest
of the complete request. The HTTP path participates in that digest. The gateway
recomputes and compares the digest before looking up the graph-derived target.
Arbitrary target URLs are never accepted.

### Token, Key, Or Log Leakage

Compact grants, signatures, private keys, and raw credentials must not enter
graphs, runtime-effect descriptors, events, observations, read models, logs,
errors, or route responses. Durable records may retain only bounded
correlation, key id, `jti`, and digest evidence.

### Compromised Transport

An intercepted or modified request still requires a valid grant whose audience,
gateway, request digest, and lifetime all match. Transport security remains
required but is not the semantic authorization decision.

### Stale Verification Key

Every grant names a key id. The verifier accepts only explicitly configured,
currently trusted verification keys. Rotation and durable key delivery belong
to the Durable Secrets and `#1144` work; production must fail closed until that
custody exists.

## Health Disclosure

The canonical first-pass policy is:

```text
/health/live   -> minimal public process liveness
/health/ready  -> delegated capability required
target count   -> never public
```

Public liveness must reveal no target names, target count, workspace,
authority, or secret material. Readiness is protected because it reveals that
the gateway is configured to serve a runtime island.

## Non-Goals

This capability does not authorize:

- arbitrary HTTP or TCP proxying;
- caller-selected URLs or audiences;
- graph mutation;
- server reconfiguration;
- deployment execution;
- gateway-owned graph truth;
- cross-restart replay protection;
- mutating control-plane variables.

Mutating server controls require durable nonce/idempotency state, stronger
approval and recovery laws, and their own explicit threat model. They are not
an extension of this read-only grant by adding another string-valued command.
