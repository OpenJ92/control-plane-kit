Source: [test_management_bootstrap_planning.py](../../../../control-plane-kit-core/tests/test_management_bootstrap_planning.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This suite uses the actual graph-pair compiler and inspects its returned DAG;
fixtures do not implement a second planner. It covers initial and retained
management observation ordering, exact graph-bound references, unsupported
changes and real service dependency cycle refusal.

`ManagedConfigurationUpdateTests` adds #1970's new-law target: exactly one
existing public JSON application file changes content while the strict V2
wrapper and all other graph material remain fixed. It checks deterministic
disruptive workload reconciliation before desired protected health, retained
management-path ordering, input preservation and no infrastructure replacement.
Equal graphs remain empty. #1971 retargets these laws to the common managed-update
compiler, which accepts the closed content/add/remove union. The obsolete
assertion that this entry point rejects valid content is intentionally replaced
with acceptance; useful add/remove behavior and refusal laws remain.
Graph-valid refusal controls cover provenance-only changes, wrapper/slot/extra
material changes, wrong receiver targets, data/secret/authority consumers and
independent verification. The active socket-secret case uses ordinary topology
compilation to prove that effective delivered material is excluded too.
These are pure planner laws, not admission, installation or provider evidence.

#1865 restores node-suppression coverage independently of the existing
runtime-suppression law. For external and attached gateways/connectors, the
runtime and the other management node still start and ingress allocation still
exists. The suppressed node gains neither StartNode nor WaitForHealthy, and
its missing structural start excludes fresh GATEWAY_INGRESS_READY refinement.
Existing GATEWAY_LOCAL_READY and AUTHENTICATED_MANAGEMENT_PATH obligations remain;
their presence is not observed success, adoption, or execution authority.
