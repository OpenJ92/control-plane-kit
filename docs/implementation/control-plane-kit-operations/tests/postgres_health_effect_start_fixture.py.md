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
