from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class OperationsPackageGateTests(unittest.TestCase):
    def test_postgres_fixture_uses_ephemeral_storage_and_volume_cleanup(self) -> None:
        gate = (PACKAGE_ROOT / "test.sh").read_text(encoding="utf-8")

        self.assertIn('docker rm -fv "$POSTGRES_CONTAINER"', gate)
        self.assertIn(
            "--tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=1g",
            gate,
        )


if __name__ == "__main__":
    unittest.main()
