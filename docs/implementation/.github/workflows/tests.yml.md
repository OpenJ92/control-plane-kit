Source: [.github/workflows/tests.yml](../../../../.github/workflows/tests.yml).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This workflow gives Core and Operations separate package-gate jobs on pull
requests, manual dispatch, and pushes to main, develop and roadmap branches.
It delegates behavior to each package's `test.sh`; the workflow does not define
a second suite. Same-workflow/ref concurrency cancels an earlier run, so a
cancelled run is not completed validation evidence.

Operations checks out architecture-testing at the exact commit also required
by its gate, supplying `CPK_ARCHITECTURE_TESTING_ROOT`. Change those two selections
together when adopting that dependency. The gate checks both HEAD and checkout
cleanliness, mounts the workflow as evidence, and runs the real Postgres suite.
See [the gate](../../../../control-plane-kit-operations/test.sh) and
[its evidence tests](../../../../control-plane-kit-operations/tests/test_package_gate.py).
Core uses [its own gate](../../../../control-plane-kit-core/test.sh).

The jobs grant GitHub contents-read permission and execute repository scripts
with Docker available on the runner. Those scripts own container creation,
mounts and cleanup; contents-read is not a restriction on Docker authority.
Operations uses named disposable database/network resources. This workflow is
package evidence, not proof of a public grandparent/child deployment or teardown.
No image publication or public ingress is declared here.
