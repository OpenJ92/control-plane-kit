Source: [control-plane-kit-core/tests/test_process_operational_contract.py](../../../../control-plane-kit-core/tests/test_process_operational_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Seven process-contract tests without a process

The fixture composes all six dependency kinds, operator-read HTTP routes, an MCP
contract and one verification HTTP check. These are declarations; no server,
database or worker is started.

The tests check default liveness/readiness kinds, paths and disclosure; dependency
order and HTTP/MCP types; a complete descriptor round trip with observation and
shutdown defaults; and rejection of one extra top-level descriptor key. Negative
cases reject a sensitive evidence-key name, a liveness value in the readiness
slot, missing HTTP/MCP contracts for declared dependencies, destructive retained-
data policy and disabled shutdown observation recording.

The final case checks selected absent implementation/secret words and invokes
the [shared security assertion](./contract_security_assertions.py.md). That helper
walks mappings/sequences for a fixed set of raw-secret field names and checks
repr text for a fixed set of value markers. It is a fixture assertion, not
arbitrary secret detection or a runtime redactor.

Despite the descriptor test's name, this file does not exercise every size or
malformed-value boundary. Empty status tuples, arbitrary-length paths/evidence
keys, duplicate dependency evidence keys, optional dependency combinations,
unchecked optional direct-constructor fields and timeout edge cases require
separate assertions. Retained-data preservation and observation recording are
checked as declared values, not by shutting down a real process.

Full 155-line file and full
[process-contract owner](../src/control_plane_kit_core/operations/process.py.md)
read, with selected imported contract owners and the full assertion helper.
There is no executed health, authorization, transaction, event or cleanup
evidence in this documentation review; no tests or runtime actions were run.
