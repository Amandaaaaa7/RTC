"""Vendor-neutral contracts for real-time social interaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


VoiceEventType = Literal["voice_onset", "voice_end", "interrupt", "cloud_state"]
DialogueEventType = Literal[
    "cloud_state", "transcript", "response_text", "response_audio", "error"
]


@dataclass(frozen=True)
class Affect:
    """A compact, bounded affect representation used by all embodiments."""

    valence: float = 0.0
    arousal: float = 0.0

    def __post_init__(self) -> None:
        if not -1.0 <= self.valence <= 1.0:
            raise ValueError("affect.valence must be in [-1, 1]")
        if not 0.0 <= self.arousal <= 1.0:
            raise ValueError("affect.arousal must be in [0, 1]")


@dataclass(frozen=True)
class VoiceEvent:
    """A timestamped event at the boundary between sensing and policy."""

    trace_id: str
    turn_id: int
    sequence: int
    type: VoiceEventType
    created_at_ns: int
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DialogueEvent:
    """A normalized backend result. No provider protocol leaks above this type."""

    trace_id: str
    turn_id: int
    type: DialogueEventType
    created_at_ns: int
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialMotionIntent:
    """A safe, expiring social intent rather than a hardware command."""

    trace_id: str
    turn_id: int
    kind: Literal["orient", "listen", "acknowledge", "comfort", "disengage"]
    target_person_id: str | None
    created_at_ns: int
    deadline_ms: int
    expires_at_ns: int
    commitment_level: Literal["orient", "acknowledge", "affect", "semantic_claim"]
    interruptibility: Literal["immediate", "boundary", "none"]
    safety_class: Literal["passive", "upper_body", "mobile"]
    affect: Affect
    source: Literal["local_reflex", "cloud", "system"] = "system"
    priority: int = 0

    def __post_init__(self) -> None:
        if self.deadline_ms < 0:
            raise ValueError("deadline_ms must be non-negative")
        expected = self.created_at_ns + self.deadline_ms * 1_000_000
        if self.expires_at_ns != expected:
            raise ValueError("expires_at_ns must equal created_at_ns + deadline_ms")

    def expired(self, now_ns: int) -> bool:
        return now_ns > self.expires_at_ns
