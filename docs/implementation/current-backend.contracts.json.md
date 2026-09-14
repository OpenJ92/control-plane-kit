Source: [current-backend.contracts.json](../../current-backend.contracts.json).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This manifest declares the static architecture/composition checks for the backend
selected by [current-backend.lock.json](../../current-backend.lock.json). It does
not independently choose source commits: the lock selects Servers, whose selected
coordinate manifest supplies the upstream commits. A newer checkout or SDK pin
does not silently replace that backend selection.

Five distributions own conventional source globs, module prefixes, allowed
internal dependencies and forbidden import prefixes. The server-products owner
includes both the shared package and product-specific namespaces. Five named
pin surfaces cover selected Interpreter/Servers metadata and product Dockerfiles;
the [validator](current_backend/contracts.py.md) scans matching archive URLs in
their full text. This pin check and the base-dependency import graph are distinct:
an optional dependency can be a required pin surface without becoming a base
declared graph edge.

Six protocol records connect runtime, authority, ingress, gateway-probe and
secret-provider class/method names across repositories. They express a structural
presence requirement, not an executable interface/type checker or proof of
authorization and secret handling. Changing a class path or method requires
review of the selected counterpart; maintaining this JSON alone cannot make
separate repository revisions compatible.

The single acceptance entry names the cpk-server HTTP/MCP source-built smoke and
residue scripts. Its provider-mutating, published-digest, diagnostic-only and
application-mock flags are false, and cpk-server is the declared authoritative
caller. These are intended evidence classifications checked partly against
script text. They neither authorize execution nor prove that a source-live
scenario ran. The [runner](../../current_backend/runner.py) owns actual stage
planning, execution and result reporting.

The loader enforces this schema's exact fields and known distribution inventory;
the [contract-test fixture](current_backend/tests/test_contracts.py.md) exercises
selected positive/negative laws against it. A new distribution or broader
evidence category needs coordinated validator, fixture and runner review rather
than an unreviewed extra JSON record. No source pin, runtime authority or
executable behavior changes accompany this companion.
