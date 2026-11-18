Source: [health_signing_authority_fixture.py](../../../../control-plane-kit-operations/tests/health_signing_authority_fixture.py).

The reload fixture inherits the explicitly registered selected receiver
artifacts and composes the same test decoder registry into the real reload
service. It preserves original preparation, interval, owner substitutions,
history snapshots and transaction assertions. Decoder construction adds no
database or provider work. Native implementation validation remains pending.
