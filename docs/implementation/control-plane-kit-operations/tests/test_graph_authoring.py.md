Source: [control-plane-kit-operations/tests/test_graph_authoring.py](../../../../control-plane-kit-operations/tests/test_graph_authoring.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These seven tests exercise GraphAuthoringService with real PostgreSQL and synthetic Core product/topology values. setUp trusts CPK_OPERATIONS_TEST_DATABASE_URL, installs the current schema and truncates workspaces with CASCADE. It does not create a unique per-test schema; only the owning Docker-backed package harness supplies appropriate disposable database isolation. Closing connections is not cleanup authority over operational data.

The happy path registers a product, compiles a one-node DockerRuntime topology as pure values and publishes desired graph/projection/generation. A fresh UoW reads the persisted graph and identity projection. No container, image pull, registry request or workload runs: the OCI reference and runtime label are fixture data. New-UoW reads prove persistence across transactions, not process restart.

Negative cases reject absent/revoked workspace registration, registration only in another workspace and a different descriptor digest under the same product identity. Selected cases also assert zero graph rows or an unassigned desired pointer. These prove precise reference admission for the supplied product-instantiated graphs, not exhaustive graph semantic validation, all-table immutability or arbitrary malicious metadata rejection.

The selectable-products case returns only the active registration with expected display name/description. It does not independently scan all returned strings for secrets. The stale-pointer test starts with an existing desired graph and requires the new graph to be absent after rejection. Despite its rollback-oriented name, the source's tuple comparison occurs before graph insertion; this case is not an injected failure after a real write.

The [owner](../src/control_plane_kit_operations/graph_authoring.py.md) has no standalone session/idempotency/authentication policy. These tests use explicit actor/workspace values and do not traverse HTTP/MCP, approval or execution. Other tests own command replay, projection-only publication and catalogue selection. The local row-count helper permits only cpk_graph_versions; it is not a broad inventory tool.

Full 373-line file and full233-line owner read; selected actual graph/projection record/store and planning wrapper contracts checked. No tests, host imports, database, Docker, provider or credential actions were run for this companion. Executable validation remains the unmodified Operations package suite when separately released.
