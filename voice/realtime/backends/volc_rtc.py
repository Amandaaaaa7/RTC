"""Reserved adapter slot for a future Volcengine RTC-room implementation.

The application imports this type only as a contract marker. It deliberately
does not initialise RTC credentials, rooms, or media devices yet.
"""

from __future__ import annotations

import numpy as np

from ..dialogue_backend import DialogueBackend, DialogueEventCallback


class VolcRtcDialogueBackend(DialogueBackend):
    """Future provider implementation; safe to construct but not to start."""

    def __init__(self, *args, **kwargs) -> None:
        self._callback: DialogueEventCallback | None = None

    def set_event_callback(self, callback: DialogueEventCallback) -> None:
        self._callback = callback

    def start(self) -> None:
        raise NotImplementedError("Volc RTC room integration has not been enabled yet")

    def start_turn(self, turn_id: int, trace_id: str) -> None:
        raise NotImplementedError("Volc RTC room integration has not been enabled yet")

    def push_audio(self, turn_id: int, frame: np.ndarray) -> None:
        raise NotImplementedError("Volc RTC room integration has not been enabled yet")

    def end_turn(self, turn_id: int) -> None:
        raise NotImplementedError("Volc RTC room integration has not been enabled yet")

    def cancel_turn(self, turn_id: int) -> None:
        raise NotImplementedError("Volc RTC room integration has not been enabled yet")

    def stop(self) -> None:
        return None

    def health(self) -> dict:
        return {"implemented": False, "provider": "volc_rtc"}
