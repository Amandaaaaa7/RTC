"""Single authority for turn ownership, interruption, and stale cloud results."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .contracts import DialogueEvent, SocialMotionIntent, VoiceEvent


@dataclass(frozen=True)
class ArbitrationResult:
    accepted: bool
    cancel_turn_id: int | None = None
    intents: tuple[SocialMotionIntent, ...] = field(default_factory=tuple)


class ResponseArbiter:
    """Reject stale results before they reach sound, eyes, or future motors."""

    def __init__(self) -> None:
        self._active_turn: int | None = None
        self._active_trace_id = ""
        self._state = "idle"
        self._cancelled: set[int] = set()

    @property
    def active_turn(self) -> int | None:
        return self._active_turn

    def on_voice_event(self, event: VoiceEvent, local_intents: list[SocialMotionIntent]) -> ArbitrationResult:
        if event.type == "voice_onset":
            previous = self._active_turn
            cancel = previous if previous is not None and previous != event.turn_id else None
            if cancel is not None:
                self._cancelled.add(cancel)
            self._active_turn = event.turn_id
            self._active_trace_id = event.trace_id
            self._state = "listening"
            return ArbitrationResult(True, cancel, tuple(local_intents))
        if event.type == "voice_end" and event.turn_id == self._active_turn:
            self._state = "transcribing"
            return ArbitrationResult(True, intents=tuple(local_intents))
        return ArbitrationResult(False)

    def on_dialogue_event(self, event: DialogueEvent) -> ArbitrationResult:
        if event.turn_id != self._active_turn or event.trace_id != self._active_trace_id:
            return ArbitrationResult(False)
        if event.turn_id in self._cancelled:
            return ArbitrationResult(False)
        if event.type == "cloud_state":
            self._state = str(event.payload.get("state", "unknown"))
        elif event.type == "error":
            self._state = "error"
        return ArbitrationResult(True)

    def interrupt_active_turn(self) -> int | None:
        if self._active_turn is None:
            return None
        cancelled = self._active_turn
        self._cancelled.add(cancelled)
        self._state = "idle"
        return cancelled

    def health(self) -> dict:
        return {
            "active_turn": self._active_turn,
            "state": self._state,
            "cancelled_turns": sorted(self._cancelled)[-16:],
            "timestamp_ns": time.monotonic_ns(),
        }
