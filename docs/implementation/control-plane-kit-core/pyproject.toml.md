Source: [control-plane-kit-core/pyproject.toml](../../../control-plane-kit-core/pyproject.toml).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This package declaration selects setuptools discovery from src for
control_plane_kit_core, Python >=3.11 and the Core distribution metadata. It
declares Jinja2 and PyYAML lower bounds plus an exact rfc8785 version. Do not
describe the whole environment as pinned: installed/build versions depend on
the consuming environment and its own lock/build evidence.

Jinja2/PyYAML support configuration rendering/validation; rfc8785 supplies
canonical bytes used in fingerprints. A dependency-only change can therefore
change a contract without editing its source owner. Review the
[configuration rendering](src/control_plane_kit_core/configuration_rendering.py.md)
and [runtime intent](src/control_plane_kit_core/runtime_effect_observation.py.md)
companions and notify adopting consumers. This file neither installs Docker
adapters nor permits an Operations import into Core; the package-name glob
must not become an ownership escape hatch.
