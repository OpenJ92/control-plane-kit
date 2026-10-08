Source: [test_management_bootstrap_planning.py](../../../../control-plane-kit-core/tests/test_management_bootstrap_planning.py).

This suite uses the actual graph-pair compiler and inspects its returned DAG;
fixtures do not implement a second planner. It covers initial and retained
management observation ordering, exact graph-bound references, unsupported
changes and real service dependency cycle refusal.

#1865 restores node-suppression coverage independently of the existing
runtime-suppression law. For external and attached gateways/connectors, the
runtime and the other management node still start and ingress allocation still
exists. The suppressed node gains neither StartNode nor WaitForHealthy, and
its missing structural start excludes fresh GATEWAY_INGRESS_READY refinement.
Existing GATEWAY_LOCAL_READY and AUTHENTICATED_MANAGEMENT_PATH obligations remain;
their presence is not observed success, adoption, or execution authority.
