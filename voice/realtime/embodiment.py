"""Embodiment adapters. Policy produces intents; adapters touch hardware."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from .contracts import SocialMotionIntent


class DollAdapter:
    """Adapter for the existing EyeDisplay and serial speaker playback queue."""

    def __init__(self, eye_display=None, speaker_module=None, volume: float = 0.5,
                 mic_monitor=None, pause_callbacks_during_playback: bool = True) -> None:
        self.eye = eye_display
        if speaker_module is None:
            import speaker as speaker_module  # Delayed: tests do not need ALSA.
        self.speaker = speaker_module
        self.volume = volume
        self.mic = mic_monitor
        self.pause_callbacks_during_playback = pause_callbacks_during_playback
        self._last_state = "idle"

    def execute(self, intent: SocialMotionIntent) -> bool:
        if intent.safety_class != "passive" or intent.expired(time.monotonic_ns()):
            return False
        state = {
            "orient": "recording",
            "listen": "listening",
            "acknowledge": "vad_triggered",
            "comfort": "speaking",
            "disengage": "idle",
        }[intent.kind]
        self._set_eye_state(state, intent.affect.valence)
        return True

    def apply_cloud_state(self, state: str) -> None:
        mapped = {
            "listening": "listening",
            "transcribing": "asr_pending",
            "thinking": "llm_pending",
            "speaking": "speaking",
            "error": "error",
            "idle": "idle",
        }.get(state, "idle")
        self._set_eye_state(mapped)

    def play_pcm(self, pcm: bytes, sample_rate: int, channels: int, sample_width: int):
        self._set_eye_state("speaking")
        paused = self.mic is not None and self.pause_callbacks_during_playback
        if paused:
            self.mic.pause_callbacks()
        handle = self.speaker.play_buffer(
            pcm, sample_rate=sample_rate, channels=channels, sample_width=sample_width,
            volume=self.volume, blocking=False,
        )
        if paused:
            if handle is None:
                self._resume_after_playback(None)
            else:
                threading.Thread(target=self._resume_after_playback, args=(handle,), daemon=True,
                                 name="realtime-capture-resume").start()
        return handle

    def interrupt(self) -> None:
        # Current speaker handles do not support hard cancellation. Clearing the
        # visual speaking state is immediate; the backend stops submitting future
        # sentence chunks, providing the declared boundary interruption behaviour.
        self._set_eye_state("listening")

    def _resume_after_playback(self, handle) -> None:
        if handle is not None:
            handle.wait()
        try:
            self.mic.drain_all_queues()
            self.mic.resume_callbacks()
        except Exception as exc:
            print(f"[REALTIME] 恢复麦克风回调失败: {exc}")

    def _set_eye_state(self, state: str, emotion: str | None = None) -> None:
        self._last_state = state
        if self.eye is not None:
            self.eye.on_voice_state(SimpleNamespace(state=state, emotion=emotion, text=None))

    def health(self) -> dict:
        return {
            "last_eye_state": self._last_state,
            "pause_callbacks_during_playback": self.pause_callbacks_during_playback,
            "audio_metrics": self.speaker.get_playback_metrics(),
        }


class A3Adapter:
    """Future A3 port. It intentionally does not issue any motor command yet."""

    def execute(self, intent: SocialMotionIntent) -> bool:
        return False

    def health(self) -> dict:
        return {"implemented": False, "capabilities": []}
