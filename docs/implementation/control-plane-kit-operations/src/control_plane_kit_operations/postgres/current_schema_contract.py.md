Source: [current_schema_contract.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
Maintain this companion alongside its source.

The frozen semantic contract mirrors the current SQL: signing keys admit the
four old purposes plus workload health read and gateway health-read transit;
secret-use authorizations append their two exact health signing intents. Only
these two constraints change. Rotation keeps four old purposes; relation, column,
index, foreign-key and other constraint identities are unchanged.

The fixed contract fingerprint is generated from canonical domain/version-tagged
JSON of these values. The owning schema tests independently name the two changed
expressions, derive the prospective fingerprint and compare fixed goldens, SQL
and observed PostgreSQL truth. The atlas header pins the same contract.

This is the current fresh-store contract, not a historical migration program.
Object-free install and exact-current query-only verification remain the only
accepted installer states. Incompatible existing stores are inspected and refused
without repair or data loss; a new application is not promised to serve an older
schema. No live database or provider mutation is part of this source change.
