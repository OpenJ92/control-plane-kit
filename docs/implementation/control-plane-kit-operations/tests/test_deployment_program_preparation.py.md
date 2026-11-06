Source: [test_deployment_program_preparation.py](../../../../control-plane-kit-operations/tests/test_deployment_program_preparation.py).
Maintain this companion alongside its source.

Existing tests use real PostgreSQL services to protect composed preparation,
plan/approval/no-change results, interruption/replay and bounded failures. #1875
adds exact caller-proposed graph identity through persisted graph, plan and
approval, unchanged submitted descriptor, same-command replay and changed-ID
conflict. A historical request-shape digest assertion protects omitted-field
compatibility without adding null to prior intent. Saved-source preparation
must reject a proposed new identity rather than silently override its reference.

These targets reuse existing services and graph scenarios. They do not generate
wrapper bytes, call providers or establish Servers receiving composition. The
normal owning gate must distinguish genuine missing identity behavior from
fixture/apparatus failures before implementation and later prove all targets green.
