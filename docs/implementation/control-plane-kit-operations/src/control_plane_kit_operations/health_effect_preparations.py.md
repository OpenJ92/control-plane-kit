Source: [health_effect_preparations.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/health_effect_preparations.py).
Maintain this companion alongside its source.

The frozen twelve-field record retains one unsigned health request and transit /
workload grant pair, the original attempt commitment, both executable projection
identities and the four retained key/use registration identities. Workspace,
logical request and digest derive from the request. Original immutable intent
owns plan, graph side and execution coordinates; actor derives from the use pair.

The closed `health-effect-preparation.v1` envelope uses the public Core codecs.
Exact nominal reconstruction rejects forged nested values and subclasses before
SQL. Canonical decoding rejects duplicate keys, nonfinite values, invalid UTF-8,
unknown fields, noncanonical bytes and envelopes above 16,384 bytes. Existing Core
limits apply to each embedded value. Owner references preserve their wider text
language; health wire identifiers retain the narrower Core language.

The request, digest, target, declaration, runtime, health kind and both validity
triples must agree. `health_effect_attempt_wire_id` hashes the full structured
identity with a dedicated domain and produces a bounded no-colon identifier.
No clock, fresh identifier, signature or current authority is created here.
Expired unsigned evidence remains valid historical material.

All record fields are repr-protected. Expected input failures become fixed errors
raised outside candidate exception contexts. The persistence codec is protected
material, not a user-facing descriptor or logging API. Tests protect exact byte
round trips, maximal lawful identifiers, independent substitutions and malformed
nominal/canonical inputs. #1852 owns actor admission and first-start composition;
this value grants no permission by itself.
