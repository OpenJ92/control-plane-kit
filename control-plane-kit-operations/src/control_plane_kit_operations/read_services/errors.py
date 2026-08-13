"""Errors raised while projecting durable read models."""


class ReadModelError(ValueError):
    """Raised when durable truth cannot support a requested read model."""
