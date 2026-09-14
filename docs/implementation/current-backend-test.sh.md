Source: [current-backend-test.sh](../../current-backend-test.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This is the stable shell entrance for the backend gate. It locates and enters
its repository directory, disables Python bytecode writes, and replaces itself
with `python3 -m current_backend.runner`, forwarding arguments unchanged.
It neither creates an environment nor selects Docker or tests itself.

The [runner](../../current_backend/runner.py) owns argument parsing, exact-source
resolution/materialization, stage effects and report writing. Its CLI selects
the repository's lock and contracts; local-repository arguments change where
exact Git objects are read, not permission to test arbitrary dirty source.
The existing [source-lock companion](current_backend/source_lock.py.md) explains
that boundary. Python availability is a prerequisite of this orchestration
entrance; invoking it is not a harmless static check or permission to substitute
host execution for package-owned Docker suites.

The shell's `exec` propagates the runner's termination directly. A missing Python
or early runner failure need not produce a gate report. Report existence and
stage success must be assessed from the runner's actual result, not from this
wrapper having started.
