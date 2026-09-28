"""Metadata shared by allowlisted agent skills."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InputLimit:
    """A named character limit for one skill input."""

    key: str
    label: str
    max_chars: int

    def __post_init__(self) -> None:
        if not self.key or not self.label or self.max_chars <= 0:
            raise ValueError("Skill input limits require a key, label, and positive max_chars.")


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """Static, reviewable configuration for one model-backed capability."""

    key: str
    title: str
    description: str
    operation: str
    input_limits: tuple[InputLimit, ...]
    max_output_tokens: int
    timeout_seconds: float
    external_data: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.key or not self.title or not self.operation:
            raise ValueError("Skills require a key, title, and operation.")
        if self.max_output_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("Skill output and timeout limits must be positive.")
        keys = [item.key for item in self.input_limits]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Skill {self.key!r} contains duplicate input limit keys.")

    def input_limit(self, key: str) -> InputLimit:
        """Return one declared input limit or fail during development."""
        for item in self.input_limits:
            if item.key == key:
                return item
        raise KeyError(f"Skill {self.key!r} has no input limit named {key!r}.")
