Source: [current_backend/tests/test_runner.py](../../../../current_backend/tests/test_runner.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The suite combines the [contract fixture](test_contracts.py.md) with a temporary
shell program. It keeps the real internal contract stage but replaces every
external command with that shell, clears stage environments and supplies synthetic
resolved commits/runner HEAD. Real subprocess streaming and report writing are
therefore exercised without package installation, Docker or HTTP/MCP execution.

Cases preserve exact stage order, first-failure stopping, residue-stage failure,
package-integrity line counting, command-start failure reporting and rejection
of overclaimed source-live or mutable-root plans. The early Operations failure
explicitly expects both source-live and residue to be absent. The final residue
stage is not unconditional compensation. Resource-name checks inspect the
builder's environment values; they do not verify ownership on a Docker daemon.

The shell emits a fake ordinary test count, exact/absent/duplicate integrity
lines and a sensitive canary. The JSON report must omit the canary because child
logs are not stored in it. This does not establish redaction of streamed output,
which the fixture captures in StringIO. Environment tests cover named CPK and
provider overrides, not every ambient secret-shaped variable. Similarly, the
test named for a bounded report does not exercise the 128-KiB rejection boundary
or crash/concurrent-writer behavior.

The complete 370-line owner, including shell and context fixtures, was read.
These are meaningful orchestration laws with deliberately simulated package
outcomes; do not label them source-live, immutable-source verification or cleanup
proof. No tests were executed for this companion. See [the runner](../runner.py.md)
for uncaught-error, process-lifetime and report-durability limits.
