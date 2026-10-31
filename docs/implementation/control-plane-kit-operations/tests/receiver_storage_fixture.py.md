Source: [receiver_storage_fixture.py](../../../../control-plane-kit-operations/tests/receiver_storage_fixture.py).
Maintain this companion alongside its source.

Setup-only real PostgreSQL fixture constructs valid Core receiver configurations, stored graph/projection pairs, structural action/session provenance and optional saved draft ownership. It uses the ordinary Operations Docker suite. It allocates only disposable test identities and supplies no C admission policy.
