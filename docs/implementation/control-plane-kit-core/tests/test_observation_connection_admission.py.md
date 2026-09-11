Source: [control-plane-kit-core/tests/test_observation_connection_admission.py](../../../../control-plane-kit-core/tests/test_observation_connection_admission.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Observing a runtime does not delegate access to a workload

Typed connection material is accepted independently of process deliveries for
selected node/runtime start and stop operations. A process-delivery declaration
cannot stand in for the observer's TLS connection carrier. When connection
grants require a carrier, absent, malformed or foreign carriers fail, and mixed
connection/product/pull grants are checked
without discarding unrelated domains to manufacture success.

Fresh grants and changed private connection references preserve the committed
runtime intent, fingerprint and public observation descriptor. This is an
identity law: connection material is outside that committed intent. It is not
permission to rotate credentials or proof of successful reauthorization.
Selected repr/descriptor checks hide those private references.

Full 162-line file read with the complete
[authority owner](../src/control_plane_kit_core/runtime_authority.py.md),
previously read [observation owner](../src/control_plane_kit_core/runtime_effect_observation.py.md),
its mixed-grant check and real imported grant helpers. Local/default and partial
connection contexts are intentionally structurally valid. No test resolves
secrets, contacts Docker or proves complete usable TLS material.
