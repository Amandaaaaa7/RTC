"""Dialogue backend boundary. Upper layers never import a vendor SDK."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

from .contracts import DialogueEvent


DialogueEventCallback = Callable[[DialogueEvent], object]


class DialogueBackend(ABC):
    """Full-turn dialogue contract used by the response arbiter."""

    @abstractmethod
    def set_event_callback(self, callback: DialogueEventCallback) -> None:
        ...

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def start_turn(self, turn_id: int, trace_id: str) -> None:
        ...

    @abstractmethod
    def push_audio(self, turn_id: int, frame: np.ndarray) -> None:
        ...

    @abstractmethod
    def end_turn(self, turn_id: int) -> None:
        ...

    @abstractmethod
    def cancel_turn(self, turn_id: int) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def health(self) -> dict:
        ...
