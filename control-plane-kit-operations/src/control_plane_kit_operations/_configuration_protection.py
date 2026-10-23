"""Private protective set with an independently proven retained root."""
from dataclasses import dataclass

from control_plane_kit_operations.configuration_preparation import (
    ConfigurationRefEvidence, _require,
)


@dataclass(frozen=True)
class _ProtectiveConfigurationAllocation:
    birth: ConfigurationRefEvidence
    claims: tuple[ConfigurationRefEvidence, ...]

    def __post_init__(self):
        _require(type(self.birth) is ConfigurationRefEvidence)
        ConfigurationRefEvidence.__post_init__(self.birth)
        _require(self.birth.identity == self.birth.birth_identity)
        _require(type(self.claims) is tuple and len(self.claims) <= 64)
        keys = []
        for claim in self.claims:
            _require(type(claim) is ConfigurationRefEvidence)
            ConfigurationRefEvidence.__post_init__(claim)
            _require(claim.ref == self.birth.ref and claim.birth_identity == self.birth.identity
                and claim.birth_artifact_id == self.birth.ref.artifact_id)
            keys.append((claim.identity.run_id.value, claim.identity.activity_id,
                claim.identity.attempt, claim.ref.artifact_id))
        _require(keys == sorted(set(keys)))
