Source: [historical_schema_fixture.py](../../../../control-plane-kit-operations/tests/historical_schema_fixture.py).

#2095 test support loads complete original historical SQL with strict byte
checks. Independent semantic goldens pin each source, the shared old target and
fresh current schema. Catalog observations reuse the existing owner's bounded
semantic PostgreSQL query; namespace snapshots prove data and object identity
survive refused startup/upgrades and rolled-back DDL. This fixture does not
implement migration, receipt/replay or authorization behavior.

Original commits and SQL hashes are recorded alongside the SQL fixtures.
Executable evidence comes only from the established Operations Docker suite.
