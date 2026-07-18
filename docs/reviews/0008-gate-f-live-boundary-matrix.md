# Roadmap 0008 Gate F Live Boundary Matrix

This matrix identifies the executable proof for each security, observation,
publication, ownership, and retention law exercised by Gate F. It does not
create another runtime model. The named tests constrain the canonical adapters,
effects, stores, projections, and live deployment program directly.

| Law | Executable proof | Required result |
| --- | --- | --- |
| Control mutation requires authentication | `tests/test_control_http_live.py`, `./gate-d-live-test.sh` | Missing or invalid bearer authority fails closed; the active route is unchanged. |
| Secrets remain references outside transport | `tests/test_effect_material.py`, `tests/test_control_http_security.py`, `tests/test_control_http_live.py` | Plaintext secret-shaped material is rejected; representations and failures redact credentials. |
| HTTP effects are bounded and redirect-free | `tests/test_control_http_client.py`, `tests/test_control_http_live.py` | Redirects, oversized responses, timeouts, malformed payloads, and uncertain mutation outcomes remain distinct. |
| Process start is not application health | `tests/test_docker_effects.py` | Docker start records only `PROCESS_STARTED` and makes no health claim. |
| Reachability, health, and readiness are distinct | `tests/test_probe_intents.py`, `tests/test_probe_execution.py` | TCP, application-health, and readiness observations retain separate typed outcomes. |
| Observations do not rewrite desired topology | `tests/test_observation_projection.py`, `tests/test_stores.py` | Observations are graph-correlated, become explicitly stale, and remain separate from current-graph truth. |
| Private-only is the publication default | `tests/test_docker_effects.py`, `./live-test.sh` | No `--publish` argument exists without typed publication; explicit loopback publication is observable and live. |
| Mutations require proven ownership | `tests/test_docker_ownership.py`, `./gate-d-live-test.sh` | Foreign collisions fail before mutation; equivalent replay converges; live cleanup checks exact labels. |
| Stop/removal preserve retained data | `tests/test_docker_retention.py` | Compute removal does not remove named data; destruction requires a separate typed operation and owned target. |
| The public composition performs the live switch | `tests/test_gate_d_live_smoke_scenario.py`, `./gate-d-live-test.sh` | One typed edge switch advances blue to green through `Deploy` and the coordinator. |

The complete operator proof is:

```bash
./gate-f-live-test.sh
```

It runs both focused live harnesses without Docker Compose. The first proves
explicit loopback publication. The second provisions Postgres and the complete
blue/router/green topology, proves unauthorized mutation returns 401, performs
the authenticated update through `Deploy`, verifies the same public route now
serves green, and removes only label-proven owned ephemeral resources.
