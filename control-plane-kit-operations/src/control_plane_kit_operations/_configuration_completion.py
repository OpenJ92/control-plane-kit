"""Issued proof for the existing fold transaction, with no standalone writer."""
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class _PreparedConfigurationCompletion:
    stores: object
    guard: object
    original: object
    attempt: object
    outcome: object
    completion: object
    accounting: object
