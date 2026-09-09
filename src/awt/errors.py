"""Error types shared by the toolchain.

Every error carries a ``hint``: a concrete next step. Tool wrappers surface the
hint to the calling agent so a failure is actionable rather than a dead end.
"""

from __future__ import annotations


class AwtError(Exception):
    """Base class for toolchain errors."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        payload = {"error": type(self).__name__, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload

    def __str__(self) -> str:
        return f"{self.message} {self.hint}".strip()


class ConfigError(AwtError):
    """A tracker or source configuration is invalid."""


class NotFoundError(AwtError):
    """A referenced tracker or item does not exist."""


class DuplicateError(AwtError):
    """A tracker with the same name already exists."""


class SourceError(AwtError):
    """An upstream source could not be fetched or parsed."""
