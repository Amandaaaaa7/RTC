"""Non-blocking VAD event source backed by the existing MicMonitor callback."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import deque
from typing import Callable

import numpy as np

from .contracts import VoiceEvent


def normalized_rms(frame: np.ndarray) -> float:
    samples = np.asarray(frame, dtype=np.float64)
    if samples.size == 0:
        return 0.0
    return min(float(np.sqrt(np.mean(samples * samples))) / 2_147_483_647.0, 1.0)


class VoiceEventSource:
    """Turns microphone frames into onset/end events without owning ALSA."""

    def __init__(
        self,
        mic_monitor,
        on_event: Callable[[VoiceEvent], None],
        on_audio_frame: Callable[[int, np.ndarray], None],
        threshold: float = 0.02,
        silence_duration_s: float = 0.8,
        sample_rate: int = 48_000,
        block_size: int = 2_048,
        calibration_frames: int = 8,
        max_utterance_s: float = 12.0,
    ) -> None:
        self.mic = mic_monitor
        self.on_event = on_event
        self.on_audio_frame = on_audio_frame
        self.threshold = threshold
        self._silence_frames = max(1, round(silence_duration_s * sample_rate / block_size))
        self._max_turn_frames = max(1, round(max_utterance_s * sample_rate / block_size))
        self._calibration_remaining = max(0, calibration_frames)
        self._calibration_levels: list[float] = []
        self._noise_floor = 0.0
        self._effective_threshold = threshold
        self._release_threshold = threshold * 0.5
        self._active_frames = 0
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._running = False
        self._thread: threading.Thread | None = None
        self._active_turn: int | None = None
        self._next_turn = 0
        self._sequence = 0
        self._trace_id = ""
        self._silent_count = 0
        self._lock = threading.Lock()
        self._levels = deque(maxlen=32)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.mic.register_audio_callback(self.on_audio_frame_from_mic)
        self._thread = threading.Thread(target=self._run, daemon=True, name="realtime-vad")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self.mic.unregister_audio_callback(self.on_audio_frame_from_mic)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            self._active_turn = None

    def on_audio_frame_from_mic(self, frame: np.ndarray) -> None:
        """MicMonitor callback: enqueue only, never perform VAD/network work."""
        try:
            self._queue.put_nowait(np.asarray(frame).copy())
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(np.asarray(frame).copy())
            except queue.Empty:
                pass

    def health(self) -> dict:
        with self._lock:
            active_turn = self._active_turn
        return {
            "running": self._running,
            "active_turn": active_turn,
            "threshold": self.threshold,
            "effective_threshold": self._effective_threshold,
            "release_threshold": self._release_threshold,
            "noise_floor": self._noise_floor,
            "calibrating": self._calibration_remaining > 0,
            "queue_depth": self._queue.qsize(),
            "recent_rms": list(self._levels),
        }

    def _emit(self, event_type: str, turn_id: int, **payload) -> None:
        self._sequence += 1
        self.on_event(VoiceEvent(
            trace_id=self._trace_id,
            turn_id=turn_id,
            sequence=self._sequence,
            type=event_type,  # type: ignore[arg-type]
            created_at_ns=time.monotonic_ns(),
            payload=payload,
        ))

    def _begin_turn(self, level: float) -> int:
        self._next_turn += 1
        turn_id = self._next_turn
        self._trace_id = uuid.uuid4().hex
        self._active_turn = turn_id
        self._silent_count = 0
        self._active_frames = 0
        self._emit("voice_onset", turn_id, rms=level, threshold=self._effective_threshold)
        return turn_id

    def _end_turn(self, turn_id: int) -> None:
        self._emit("voice_end", turn_id)
        self._active_turn = None
        self._silent_count = 0
        self._active_frames = 0

    def _observe_idle_level(self, level: float) -> bool:
        """Calibrate and then slowly follow the room's idle noise floor."""
        if self._calibration_remaining > 0:
            self._calibration_levels.append(level)
            self._calibration_remaining -= 1
            if self._calibration_remaining == 0:
                self._noise_floor = float(np.median(self._calibration_levels))
                self._update_thresholds()
            return True
        if level < self._effective_threshold:
            if self._noise_floor <= 0:
                self._noise_floor = level
            else:
                self._noise_floor = self._noise_floor * 0.9 + level * 0.1
            self._update_thresholds()
        return False

    def _update_thresholds(self) -> None:
        # A fixed threshold cannot work in both a quiet room and a Raspberry Pi
        # close to a fan.  Keep an absolute floor, then require a small but
        # measurable margin above the observed idle level.  Release stays just
        # above idle so a room floor cannot keep a turn open forever.
        self._effective_threshold = max(self.threshold, self._noise_floor + 0.008)
        self._release_threshold = max(self._noise_floor * 1.1, self._effective_threshold * 0.5)

    def _run(self) -> None:
        while self._running:
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            level = normalized_rms(frame)
            self._levels.append(level)
            with self._lock:
                turn_id = self._active_turn
                if turn_id is None:
                    if self._observe_idle_level(level):
                        continue
                    if level < self._effective_threshold:
                        continue
                    turn_id = self._begin_turn(level)
                self.on_audio_frame(turn_id, frame)
                self._active_frames += 1
                if level >= self._release_threshold:
                    self._silent_count = 0
                else:
                    self._silent_count += 1
                    if self._silent_count >= self._silence_frames:
                        self._end_turn(turn_id)
                        continue
                if self._active_frames >= self._max_turn_frames:
                    self._end_turn(turn_id)
