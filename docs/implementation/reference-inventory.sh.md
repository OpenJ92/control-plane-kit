Source: [reference-inventory.sh](../../reference-inventory.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This Bash entry point generates the historical reference-test inventory. It
resolves the selected tag, compares it to the expected commit, archives that
tag into a temporary directory, builds the frozen Dockerfile's test target and
runs the [current inventory module](extraction_parity/inventory.py.md) in /app.
The default tag identifies the pre-server-product-extraction reference; both
tag and expected commit can be explicitly overridden. The archive command uses
the tag again after the comparison, so the two operations assume the ref remains
stable rather than holding an immutable resolved-object handle.

The selected frozen Dockerfile's package/test chain installs the legacy package
and its test dependencies and copies the historical tests into /app. It does
not execute every other Dockerfile stage. The run overrides the image's unittest
CMD with inventory collection. Frozen test source and the current read-only
extraction_parity mount are therefore different inputs, as is the current
[law override file](../../artifacts/extraction/law-overrides.json). A Git source
coordinate does not pin the base image, resolver artifacts or scanner/override
content to that same commit.

Collection imports frozen tests and can execute discovery-time code; it does
not establish a passing frozen suite. The container receives a writable output
directory mount and writes the selected inventory filename. The script creates
that directory on the host. It has build/network/filesystem/Docker effects and
is not a read-only inspection command merely because the output is JSON.

The EXIT trap attempts forced removal of the commit-prefix-derived image tag
and removes the temporary tree. The image name is shared by runs selecting the
same expected commit, and cleanup is registered before identity validation;
it can attempt image removal even when this invocation never built one. There
is no per-run ownership token or verified image-cleanup result. Concurrent runs
can also share an output path, whose writer uses a fixed .tmp sibling.

[Focused tests](extraction_parity/tests/test_reference_test_inventory.py.md)
inspect a few shell strings; they do not prove actual build, collection or
cleanup success. The committed [inventory artifact](../../artifacts/extraction/reference-tests.json)
records prior collection counts/laws, not current package, provider or
grandparent/child acceptance. No regeneration or runtime action accompanied
this companion.
