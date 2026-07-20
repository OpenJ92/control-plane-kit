from __future__ import annotations

from unittest import TestCase, main
from unittest.mock import patch

from control_plane_kit._optional import require_optional_dependencies


class OptionalDependencyTests(TestCase):
    def test_missing_dependency_names_surface_and_install_extra(self) -> None:
        with patch("control_plane_kit._optional.find_spec", return_value=None):
            with self.assertRaisesRegex(
                ModuleNotFoundError,
                r"control_plane_kit\.servers requires optional dependencies: "
                r"fastapi, httpx\. Install control-plane-kit\[http\]",
            ):
                require_optional_dependencies(
                    "control_plane_kit.servers",
                    ("fastapi", "httpx"),
                    extra="http",
                )

    def test_available_dependencies_are_accepted(self) -> None:
        with patch("control_plane_kit._optional.find_spec", return_value=object()):
            require_optional_dependencies(
                "control_plane_kit.adapters",
                ("httpx",),
                extra="http",
            )


if __name__ == "__main__":
    main()
