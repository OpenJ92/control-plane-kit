Source: [postgres_readiness_uses.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres_readiness_uses.py).

#2089 creates/reloads three metadata authorizations atomically inside an existing
caller-owned reached-check transaction: immutable password version, gateway transit
signer and workload health signer. SQL-domain correlations bind original request,
installation and check independently of caller actor/session or active key choice.
Retained use scope must match the trusted reached-command actor and original
execution session. The retained original event supplies exact authorization time,
whose epoch projection must agree with the binding's original window.
Missing roles may be created only on the caller's fresh unconsumed path. Partial
sets always refuse. Current issuance locks correlations, original key purposes,
all references then all providers, and verifies both installed receivers/keys
before the first insert. The helper never commits or calls a provider.

Historical signer recovery counts all exact installed public-key and retained
private-reference matches before checking status. It uses the existing bounded
installed verifier family and immutable registry. Current use rereads that exact
registration under its shared purpose lock and requires ACTIVE; replacement
never changes original identity. Inspection may describe an inactive original,
but cannot reissue it. Original scopes, fingerprints and timestamp remain exact.
Claims/window/JTIs reconstruct deterministically; no fresh deadline on replay.

The caller must still prove current original command/run/lease, reached-check
order and unconsumed invocation, commit, and exit the physical transaction before
private resolution. These metadata tests do not qualify that producer or SQL
dispatch. No schema, general credential model or health wire is introduced.
