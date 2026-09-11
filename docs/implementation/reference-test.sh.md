Source: [reference-test.sh](../../reference-test.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This is an effectful historical-reference reproduction entry point under the
[testing evidence classes](../TESTING.md). It resolves the selected tag, compares
it with the expected commit and archives that tag into a temporary tree. Defaults
select pre-server-product-extraction-2026-07-20 at
20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec; both values are overridable. The archive
uses the symbolic tag again after checking it, so source selection assumes the
ref remains stable across those commands.

The selected frozen test.sh first runs its packaging wrapper, builds the test
image, creates a Docker network and Postgres container, waits for database health
and runs unittest discovery in a test container. Its cleanup removes the supplied
container/network names both before execution and on exit. The packaging wrapper
builds four wheel-acceptance stages and forcibly removes four fixed image tags
before and after that work. Those packaging image names do not inherit RUN_ID.
After the frozen suite succeeds, this outer wrapper runs compileall in the test
image. These are builds, process/network/database effects and cleanup operations,
not read-only inspection of the archived repository.

Frozen source does not pin the complete runtime: the selected Dockerfile defaults
to Python 3.14 slim, upgrades pip and installs dependencies with lower-bound
requirements. PYTHON_IMAGE controls the outer evidence-tool container and the
image reference inspected for the report; it is not passed as a build argument
to the frozen Dockerfile. Postgres remains postgres:16-alpine. Image IDs are
observed after execution, not enforced as immutable inputs to every build/run.

The frozen suite's combined output is retained through head at the configured
limit plus one byte (default limit 8 MiB), with pipefail active. Overflow can fail
the pipeline; the evidence parser also rejects oversized retained output. On
suite failure, the last 200 retained lines are printed without secret redaction.
Compile and evidence-tool output do not share this capture bound, and the wrapper
has no overall execution timeout. Failure can leave an older evidence file at
the selected path rather than producing a new failure record.

Resource snapshots list all containers, networks and volumes on the selected
Docker daemon. cleanup_owned_volumes takes every volume added since the first
snapshot, refuses a candidate when a container is found using it, and removes
detached candidates. Despite its name, this is daemon-wide set subtraction,
without run labels or custody checks: concurrent unrelated additions can become
deletion candidates. The attachment check and removal are separate operations.
The normal-path cleanup failure stops execution; the EXIT trap retries cleanup
and suppresses its failure. No prune command is used, but that alone does not
establish exact run ownership or complete cleanup.

RUN_ID defaults to the expected commit's first twelve characters, so repeated
runs share outer image/container/network names unless overridden. The outer EXIT
trap forcibly removes its image tag and deletes the temporary tree; it is already
registered before identity validation, so it can attempt image removal before
this invocation builds anything. Together with the frozen scripts' initial
cleanup and fixed packaging tags, this is not an isolated concurrent-run protocol.

After normal cleanup, the wrapper records another resource snapshot and runs the
[current reference module](extraction_parity/reference.py.md) from a read-only
working-tree mount. Frozen dependency files and captured inventories are mounted
read-only; the selected evidence directory is writable. This current interpreter
is a separate input from the archived source. Its record hashes four archived
files and records summary/image/resource declarations, but does not fail solely
because final additions are nonempty. Snapshots precede the evidence container
and final EXIT cleanup; they are not a terminal audit of every wrapper effect.

The default output overwrites the historical
[reference baseline](../../artifacts/extraction/reference-baseline.json), which
currently records 1112 successful tests, zero skips, empty additions and one
cleaned volume. That committed report does not establish a current backend,
published-image or grandparent/child result. The
[focused wrapper checks](extraction_parity/tests/test_reference_parity_evidence.py.md)
inspect source strings rather than execute these effects. Full wrapper, selected
frozen test/packaging scripts, Dockerfile, pyproject and baseline were read; no
reproduction, cleanup or evidence rewrite accompanied this documentation.
