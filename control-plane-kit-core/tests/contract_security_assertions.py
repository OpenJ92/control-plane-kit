from collections.abc import Mapping, Sequence
from typing import Any
import unittest


_RAW_SECRET_FIELDS = frozenset(
    {
        "access_token",
        "api_token",
        "ciphertext",
        "client_key",
        "credential",
        "credential_value",
        "master_key",
        "password",
        "plaintext",
        "private_key",
        "secret",
        "secret_value",
        "token",
    }
)

_SECRET_VALUE_MARKERS = (
    "-----begin private key-----",
    "bearer ey",
    "postgres://operator:password@",
    "raw-provider-credential",
    "super-secret-value",
)


def assert_descriptor_excludes_secret_material(
    test_case: unittest.TestCase,
    descriptor: Any,
) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized_key = str(key).lower().replace("-", "_")
                test_case.assertNotIn(normalized_key, _RAW_SECRET_FIELDS)
                visit(nested)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item)

    visit(descriptor)
    rendered = repr(descriptor).lower()
    for marker in _SECRET_VALUE_MARKERS:
        test_case.assertNotIn(marker, rendered)
