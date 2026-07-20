# Extraction Evidence

This directory contains bounded machine-readable evidence for the external
server-product extraction. It must not contain raw test logs, environment
variables, credentials, secret values, or unbounded command output.

`reference-baseline.json` is produced by `./reference-test.sh` from the
immutable `pre-server-product-extraction-2026-07-20` tag. The runner archives
that tag into a temporary directory, executes the archived checkout's own
Docker/Postgres suite, compiles its Python sources, records immutable inputs,
and compares Docker resource inventories before and after execution.

The runner never performs global Docker cleanup. The frozen suite creates an
anonymous Postgres volume; the outer runner removes only a volume absent from
the before-snapshot, present after the run, and detached from every container.
The exact removed identity and final residue are part of the evidence.

