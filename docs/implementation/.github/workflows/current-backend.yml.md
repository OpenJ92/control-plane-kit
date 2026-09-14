Source: [.github/workflows/current-backend.yml](../../../../.github/workflows/current-backend.yml).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This job invokes the exact-backend gate on pull requests and pushes to the
specifically named extraction roadmap branch. Its push selection differs from
the current-package workflow; do not infer that every roadmap push runs it.
Full Git history is requested at checkout. GitHub permission is contents-read.

The [shell entrance](../../current-backend-test.sh.md) selects the current
checkout's runner, which resolves the backend lock and contract manifest.
Consequently the source under test is selected by those contracts, not simply
everything currently checked out by Actions. The runner owns stage execution,
failure disposition and report contents; this YAML only supplies a repository-
relative report destination and a 90-minute job timeout.

The artifact step uses `always()` and warns if the report is missing. A failure
before report writing, or interruption, can therefore leave no report; reaching
the artifact step does not prove the gate passed. The runner caps report JSON
at 128 KiB in `write_report`; the artifact action itself is not the redaction or
size validator. Review [runner.py](../../../../current_backend/runner.py) when
changing the reporting contract. This workflow does not establish separately
held live-deployment acceptance or authorize provider actions.
