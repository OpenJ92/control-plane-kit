Source: [postgres_health_effect_start_fixture.py](../../../../control-plane-kit-operations/tests/postgres_health_effect_start_fixture.py).

The shared first-start fixture now uses the receiver-trust helper to attach
explicit pinned products and selected artifacts before graph validation and
plan compilation, registers their descriptors through ProductRegistrationService,
and composes the pure test decoder into EffectAttemptStartService. Defaults
deliberately trust a different key from selected bytes. Existing approved-plan,
predecessor-event, active-key, lease and transaction setup remains intact.
No behavior assertion or state transition is replaced by a fixture model.
Historical preparation-only fixtures retain their original construction.

This is test-only profile interpretation. It proves Operations selection and
coverage, not Servers product parsing or live receiver behavior. Existing
first-start, eligibility, rollback, uncertain-commit and concurrency methods
must pass again on the implementation. The earlier missing-contract red does
not validate this fixture migration.

## O2 / #1883 current boundary

Positive live setup now creates the isolated workspace and desired graph through their real owners and uses actual execution admission. Receiver bindings, introduction actions and workspace pins are never forged. Plan/approval, lease/run and successful predecessor records remain explicitly recorded test premises; they do not prove provider effects or accepted deployment. The start intent contains the actual selected product/configuration/environment and runtime reference required by O1, rather than the former empty product tuple. corrupt_health_projection is a labeled post-setup negative premise that preserves original digest/binding/action witnesses and restores the descriptor in finally. Snapshots include receiver and graph/action history, so refusal must not repair or mutate it. Original V1 preparation fixtures are unchanged.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.
