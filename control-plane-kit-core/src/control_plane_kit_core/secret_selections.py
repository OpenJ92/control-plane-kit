"""Secret-free instance choices; unresolved authorship is never selected permission."""
from dataclasses import dataclass
import re

from control_plane_kit_core.secrets import (
    SecretReference, SecretUseIntent, SecretEnvironmentDelivery, SecretFileDelivery,
)

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_FIELDS = {"reference_id", "intent", "version_id"}
_MAX_SELECTIONS = 1024


class SecretSelectionError(ValueError):
    """A bounded current selection contract was not satisfied."""


def _require(condition):
    if not condition:
        raise SecretSelectionError("secret selection is malformed or incomplete")


def _use(reference, intent):
    _require(type(reference) is SecretReference and type(intent) is SecretUseIntent)
    _require(type(reference.reference_id) is str and len(reference.reference_id) <= 2048)
    _require(SecretReference(reference.reference_id) == reference)


@dataclass(frozen=True, slots=True)
class UnselectedSecretUse:
    reference: SecretReference
    intent: SecretUseIntent

    def __post_init__(self):
        _use(self.reference, self.intent)

    def descriptor(self):
        UnselectedSecretUse.__post_init__(self)
        return dict(reference_id=self.reference.reference_id, intent=self.intent.value, version_id=None)


@dataclass(frozen=True, slots=True)
class SecretVersionSelection:
    reference: SecretReference
    intent: SecretUseIntent
    version_id: str

    def __post_init__(self):
        _use(self.reference, self.intent)
        _require(type(self.version_id) is str and _VERSION.fullmatch(self.version_id) is not None)

    def descriptor(self):
        SecretVersionSelection.__post_init__(self)
        return dict(reference_id=self.reference.reference_id, intent=self.intent.value, version_id=self.version_id)


SecretSelection = UnselectedSecretUse | SecretVersionSelection


def secret_selection_key(value):
    return value.reference.reference_id, value.intent.value


def secret_selection_from_descriptor(document):
    valid = False
    try:
        _require(type(document) is dict and set(document) == _FIELDS)
        _require(type(document["reference_id"]) is str and type(document["intent"]) is str)
        reference, intent = SecretReference(document["reference_id"]), SecretUseIntent(document["intent"])
        value = (UnselectedSecretUse(reference, intent) if document["version_id"] is None
                 else SecretVersionSelection(reference, intent, document["version_id"]))
        valid = True
    except (ValueError, TypeError, AttributeError):
        valid = False
    _require(valid)
    return value


def secret_selections_from_descriptor(document):
    _require(type(document) is list and len(document) <= _MAX_SELECTIONS)
    return tuple(secret_selection_from_descriptor(value) for value in document)


def resolving_secret_uses(deliveries):
    uses = {}
    for value in deliveries:
        if isinstance(value, (SecretEnvironmentDelivery, SecretFileDelivery)):
            _require(type(value) in (SecretEnvironmentDelivery, SecretFileDelivery))
            _use(value.reference, value.intent)
            uses[secret_selection_key(value)] = value.reference, value.intent
    return uses


def _entries(values):
    _require(type(values) is tuple and len(values) <= _MAX_SELECTIONS)
    for value in values:
        _require(type(value) in (UnselectedSecretUse, SecretVersionSelection))
        type(value).__post_init__(value)
    keys = tuple(secret_selection_key(value) for value in values)
    _require(len(set(keys)) == len(keys))
    return {secret_selection_key(value): value for value in values}


def normalize_secret_selections(deliveries, selections):
    """Validate total coverage; never fill missing declarations at a boundary."""
    entries = _entries(selections)
    _require(set(entries) == set(resolving_secret_uses(deliveries)))
    return tuple(entries[key] for key in sorted(entries))


def author_secret_selections(deliveries, previous=(), explicit=()):
    """Common pure insertion path used by compilation and successor authoring."""
    uses = resolving_secret_uses(deliveries)
    old, choices = _entries(previous), _entries(explicit)
    _require(set(choices) <= set(uses))
    _require(all(type(value) is SecretVersionSelection for value in choices.values()))
    return tuple(choices.get(key) or old.get(key) or UnselectedSecretUse(*uses[key])
                 for key in sorted(uses))


__all__ = ["SecretSelectionError", "UnselectedSecretUse", "SecretVersionSelection",
           "SecretSelection", "secret_selection_from_descriptor", "secret_selections_from_descriptor",
           "normalize_secret_selections", "author_secret_selections"]
