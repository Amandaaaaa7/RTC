"""
VoiceModule — 统一语音交互调度器。

Flow:
1. 注册为 MicMonitor 的音频回调消费者。
2. 基于 VAD 触发录音。
3. ASR 转写 → LLM 情绪分析 → TTS 播放。
4. 通过 state callbacks 通知 EyeDisplay 等组件。
"""

import os
import threading
import time
from collections import deque
from typing import Callable

from voice.config import (
    RECORD_PATH, SAMPLE_RATE, VAD_THRESHOLD, SILENCE_DURATION,
    DEFAULT_ASR_BACKEND, DEFAULT_LLM_BACKEND, DEFAULT_TTS_BACKEND,
    SPEAKER_VOLUME, MEMORY_PATH, XP_PATH,
)
from voice.audio import CallbackVoiceRecorder, play_audio
from voice.backends.base import ASRBackend, LLMBackend, TTSBackend, VoiceState
from voice.backends.preset import PresetAudioTTS
from voice.emotion_engine import allowed_emotions, get_profile
from voice.memory import Memory
from voice.xp import XPSystem


class VoiceModule:
    """
    语音交互模块主类。

    接入 MicMonitor 回调，完成 VAD/录音/ASR/LLM/TTS 的完整链路，
    并通过状态回调与 EyeDisplay 等组件联动。
    """

    def __init__(
        self,
        mic_monitor=None,
        asr: ASRBackend | None = None,
        llm: LLMBackend | None = None,
        tts: TTSBackend | None = None,
        state_callback: Callable[[VoiceState], None] | None = None,
        enable_memory: bool = True,
        enable_xp: bool = True,
    ):
        self.mic = mic_monitor
        self.asr = asr or self._create_asr(DEFAULT_ASR_BACKEND)
        self.llm = llm or self._create_llm(DEFAULT_LLM_BACKEND)
        self.tts = tts or self._create_tts(DEFAULT_TTS_BACKEND)
        self.state_callback = state_callback

        self._recorder = CallbackVoiceRecorder(
            threshold=VAD_THRESHOLD,
            silence_duration=SILENCE_DURATION,
            sample_rate=SAMPLE_RATE,
        )
        self._worker: threading.Thread | None = None
        self._running = False
        self._current_state = VoiceState(VoiceState.IDLE)
        self._playback_started_ns = None
        self._playback_finished_ns = None
        self._playback_started_count = 0
        self._playback_finished_count = 0
        self._playback_failed_count = 0
        self._tts_pending_to_first_sample_ms = deque(maxlen=256)

        self.memory = Memory(persist_path=MEMORY_PATH) if enable_memory else None
        self.xp = XPSystem(persist_path=XP_PATH) if enable_xp else None

        self._lock = threading.Lock()

    # ---- backend factory ----

    @staticmethod
    def _create_asr(name: str) -> ASRBackend:
        if name == "dashscope":
            from voice.backends.cloud import DashScopeASR
            return DashScopeASR()
        if name == "google":
            from voice.backends.cloud import GoogleASR
            return GoogleASR()
        raise ValueError(f"不支持的 ASR 后端: {name}")

    @staticmethod
    def _create_llm(name: str) -> LLMBackend:
        if name == "dashscope":
            from voice.backends.cloud import DashScopeLLM
            return DashScopeLLM()
        raise ValueError(f"不支持的 LLM 后端: {name}")

    @staticmethod
    def _create_tts(name: str) -> TTSBackend:
        if name == "preset":
            return PresetAudioTTS(volume=SPEAKER_VOLUME)
        if name == "edge":
            from voice.backends.cloud import EdgeTTSBackend
            return EdgeTTSBackend(volume=SPEAKER_VOLUME)
        raise ValueError(f"不支持的 TTS 后端: {name}")

    # ---- state management ----

    def _set_state(self, state: str, emotion: str | None = None, text: str | None = None):
        with self._lock:
            self._current_state = VoiceState(state, emotion, text)
        if self.state_callback:
            try:
                self.state_callback(self._current_state)
            except Exception as e:
                print(f"[VOICE] 状态回调异常: {e}")

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return VoiceState(
                self._current_state.state,
                self._current_state.emotion,
                self._current_state.text,
            )

    # ---- lifecycle ----

    def start(self):
        """启动语音模块，注册 MicMonitor 回调。"""
        if self._running:
            return
        self._running = True

        if self.mic:
            self.mic.register_audio_callback(self._recorder.on_audio_frame)

        self._worker = threading.Thread(target=self._loop, daemon=True, name="voice-module")
        self._worker.start()
        print("[VOICE] 语音模块已启动")

    def stop(self):
        """停止语音模块，注销回调。"""
        self._running = False
        if self.mic:
            try:
                self.mic.unregister_audio_callback(self._recorder.on_audio_frame)
            except Exception:
                pass
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        if self.memory:
            self.memory.save()
        if self.xp:
            self.xp.save()
        self._set_state(VoiceState.IDLE)
        print("[VOICE] 语音模块已停止")

    # ---- main loop ----

    def _loop(self):
        """连续监听并处理语音交互。"""
        while self._running:
            try:
                self._run_once()
            except Exception as e:
                print(f"[VOICE] 交互异常: {e}")
                self._set_state(VoiceState.IDLE)
                time.sleep(0.5)

    def _run_once(self):
        """单次交互：等待语音 → 录音 → ASR → LLM → TTS。"""
        self._set_state(VoiceState.IDLE)

        # 1. 等待 VAD 触发
        self._recorder.start()
        self._set_state(VoiceState.VAD_TRIGGERED)

        # Busy-wait until recording has started or module is stopping
        timeout = 30.0
        start = time.time()
        while self._running and not self._recorder._recording:
            if time.time() - start > timeout:
                print("[VOICE] 等待语音超时")
                self._recorder.stop()
                return
            time.sleep(0.05)

        if not self._running:
            self._recorder.stop()
            return

        self._set_state(VoiceState.RECORDING)

        # Wait until recording finishes (VAD silence or max duration)
        while self._running and self._recorder._running:
            time.sleep(0.05)

        if not self._running:
            self._recorder.stop()
            return

        wav_path = self._recorder.stop_and_save(RECORD_PATH)
        if not wav_path or not os.path.exists(wav_path):
            return

        # 2. ASR
        self._set_state(VoiceState.ASR_PENDING)
        text = self.asr.transcribe(wav_path)
        if not text:
            print("[VOICE] ASR 未识别到文字")
            self._set_state(VoiceState.IDLE)
            return

        # 3. LLM emotion analysis
        self._set_state(VoiceState.LLM_PENDING, text=text)
        emotions = allowed_emotions()
        emotion = self.llm.analyze_emotion(text, emotions)
        profile = get_profile(emotion)

        # 4. Record in memory / XP
        if self.memory:
            self.memory.add_turn("user", text, emotion)
        if self.xp:
            self.xp.award_interaction(emotion, len(text))

        # 5. TTS. Actual playback, rather than a guessed text duration, owns
        # the speaking boundary seen by EyeDisplay.
        self._set_state(VoiceState.TTS_PENDING, emotion=emotion, text=text)
        pending_ns = time.monotonic_ns()
        self._configure_playback_callbacks(emotion, text, pending_ns)
        handle = self._speak(text, emotion)
        if handle is None or handle is False:
            self._playback_failed_count += 1
            self._set_state(VoiceState.ERROR, emotion=emotion, text=text)
            self._clear_playback_callbacks()
            return

        # This waits only in the voice worker. The shared audio worker, eye
        # animation thread and FaceTracker thread remain independently runnable.
        if hasattr(handle, "wait"):
            handle.wait()
            if getattr(handle, "error", None) is not None:
                self._playback_failed_count += 1
                self._set_state(VoiceState.ERROR, emotion=emotion, text=text)
                self._clear_playback_callbacks()
                return
        else:
            # Compatibility for an external synchronous backend that returns
            # True after finishing playback.
            self._on_playback_started(emotion, text, pending_ns, pending_ns)
            self._on_playback_finished(time.monotonic_ns(), None)

        self._set_state(VoiceState.IDLE, emotion=emotion)
        self._clear_playback_callbacks()

    def _speak(self, text: str, emotion: str):
        """Play a response for the given emotion/text."""
        return self.tts.speak(text, emotion=emotion)

    def _configure_playback_callbacks(self, emotion: str, text: str, pending_ns: int):
        set_first = getattr(self.tts, "set_first_sample_callback", None)
        if set_first is not None:
            set_first(
                lambda started_ns: self._on_playback_started(
                    emotion, text, pending_ns, started_ns
                )
            )
        set_finished = getattr(self.tts, "set_finished_callback", None)
        if set_finished is not None:
            set_finished(self._on_playback_finished)

    def _clear_playback_callbacks(self):
        for method_name in ("set_first_sample_callback", "set_finished_callback"):
            method = getattr(self.tts, method_name, None)
            if method is not None:
                method(None)

    def _on_playback_started(self, emotion: str, text: str, pending_ns: int, started_ns: int):
        with self._lock:
            self._playback_started_ns = started_ns
            self._playback_started_count += 1
            elapsed_ms = (started_ns - pending_ns) / 1_000_000
            if elapsed_ms >= 0:
                self._tts_pending_to_first_sample_ms.append(elapsed_ms)
        self._set_state(VoiceState.SPEAKING, emotion=emotion, text=text)

    def _on_playback_finished(self, finished_ns: int, error):
        with self._lock:
            self._playback_finished_ns = finished_ns
            self._playback_finished_count += 1
            self._playback_failed_count += int(error is not None)

    # ---- health check ----

    def health(self) -> dict:
        """Return a quick health snapshot."""
        return {
            "running": self._running,
            "state": self.state.state,
            "asr": self.asr.name,
            "llm": self.llm.name,
            "tts": self.tts.name,
            "mic_attached": self.mic is not None,
        }

    @staticmethod
    def _percentiles(values):
        if not values:
            return {"p50": None, "p95": None}
        ordered = sorted(values)
        return {
            "p50": round(ordered[round((len(ordered) - 1) * 0.50)], 3),
            "p95": round(ordered[round((len(ordered) - 1) * 0.95)], 3),
        }

    def get_metrics(self) -> dict:
        """Expose standard-voice playback timing through the common /status API."""
        with self._lock:
            return {
                "state": self._current_state.state,
                "tts": self.tts.name,
                "playback_started_count": self._playback_started_count,
                "playback_finished_count": self._playback_finished_count,
                "playback_failed_count": self._playback_failed_count,
                "last_playback_started_ns": self._playback_started_ns,
                "last_playback_finished_ns": self._playback_finished_ns,
                "tts_pending_to_first_sample_ms": self._percentiles(
                    tuple(self._tts_pending_to_first_sample_ms)
                ),
            }
