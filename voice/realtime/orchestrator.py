"""Lifecycle wiring for the optional real-time interaction path."""

from __future__ import annotations

import threading
from pathlib import Path

from .backends import VolcDirectDialogueBackend
from .config import RealtimeVoiceConfig
from .contracts import DialogueEvent
from .embodiment import A3Adapter, DollAdapter
from .event_source import VoiceEventSource
from .expression_coordinator import ExpressionCoordinator
from .local_reflex import LocalReflexEngine
from .local_memory import LocalConversationMemory
from .response_arbiter import ResponseArbiter


CONFIG_DIR = Path(__file__).parents[2] / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "realtime_voice.example.json"
LOCAL_CONFIG_PATH = CONFIG_DIR / "realtime_voice.local.json"


class RealtimeVoiceOrchestrator:
    """Composes new policy modules around unchanged microphone/eye/speaker APIs."""

    def __init__(self, mic_monitor, eye_display=None, config_path: str | None = None,
                 speaker_module=None) -> None:
        selected_config = config_path or (LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH)
        self.config = RealtimeVoiceConfig.from_file(selected_config)
        if self.config.backend != "volc_direct":
            raise ValueError(f"unsupported realtime backend: {self.config.backend}")
        self.backend = VolcDirectDialogueBackend(self.config.direct)
        self.memory = LocalConversationMemory(self.config.local_memory)
        self.backend.restore_history(self.memory.history_for_prompt())
        self.arbiter = ResponseArbiter()
        self.reflex = LocalReflexEngine()
        self.coordinator = ExpressionCoordinator(
            DollAdapter(
                eye_display=eye_display,
                speaker_module=speaker_module,
                mic_monitor=mic_monitor,
                pause_callbacks_during_playback=self.config.pause_callbacks_during_playback,
            ),
            A3Adapter(),
        )
        self.source = VoiceEventSource(
            mic_monitor=mic_monitor,
            on_event=self._on_voice_event,
            on_audio_frame=self._on_audio_frame,
            threshold=self.config.vad_threshold,
            silence_duration_s=self.config.silence_duration_s,
        )
        self.backend.set_event_callback(self._on_dialogue_event)
        self._running = False
        self.state = "已停止"
        self.last_info = ""
        self._ui_lock = threading.Lock()
        self._ui_messages: list[dict] = []
        self._ui_revision = 0
        self._pending_user_text: dict[int, str] = {}
        self._welcome_generation = 0

    def start(self) -> None:
        if self._running:
            return
        self.backend.start()
        self.source.start()
        self._running = True
        self.state = "等待说话"
        self.last_info = "volc_direct"
        self._touch_ui()
        print("[REALTIME] 实时语音已启动 (backend=volc_direct)")
        self._start_welcome()

    def stop(self) -> None:
        if not self._running:
            return
        self.source.stop()
        self.backend.stop()
        self._welcome_generation += 1
        self._running = False
        self.state = "已停止"
        self.last_info = ""
        self._touch_ui()
        print("[REALTIME] 实时语音已停止")

    def _on_voice_event(self, event) -> None:
        result = self.arbiter.on_voice_event(event, self.reflex.on_voice_event(event))
        if not result.accepted:
            return
        if result.cancel_turn_id is not None:
            self.backend.cancel_turn(result.cancel_turn_id)
            self.coordinator.interrupt()
        self.coordinator.execute_intents(result.intents)
        if event.type == "voice_onset":
            self.state = "正在聆听"
            self.last_info = "voice_onset"
            print(
                "[REALTIME] VAD 触发："
                f"rms={float(event.payload.get('rms', 0.0)):.4f}, "
                f"门限={float(event.payload.get('threshold', 0.0)):.4f}"
            )
            self.backend.start_turn(event.turn_id, event.trace_id)
        elif event.type == "voice_end":
            self.state = "正在识别"
            self.last_info = "voice_end"
            print("[REALTIME] VAD 结束，正在发送 ASR")
            self.backend.end_turn(event.turn_id)

    def _on_audio_frame(self, turn_id, frame) -> None:
        if turn_id == self.arbiter.active_turn:
            self.backend.push_audio(turn_id, frame)

    def _on_dialogue_event(self, event):
        result = self.arbiter.on_dialogue_event(event)
        if not result.accepted:
            return None
        if event.type == "cloud_state":
            self.state = str(event.payload.get("state", "unknown"))
            self.last_info = ""
        elif event.type == "transcript":
            self.last_info = str(event.payload.get("text", ""))[:48]
        elif event.type == "error":
            self.state = "错误"
            self.last_info = str(event.payload.get("message", ""))[:48]
            print(f"[REALTIME] {event.payload.get('message', '未知错误')}")
        self._record_ui_event(event)
        self._record_local_memory(event)
        return self.coordinator.on_dialogue_event(event)

    def health(self) -> dict:
        return {
            "running": self._running,
            "source": self.source.health(),
            "arbiter": self.arbiter.health(),
            "backend": self.backend.health(),
            "embodiment": self.coordinator.health(),
        }

    def get_metrics(self) -> dict:
        """Match camera.py's optional voice monitor protocol."""
        return self.health()

    # ---- Browser control panel contract (used only by camera.py) ----

    def get_ui_snapshot(self) -> dict:
        """Return a bounded, JSON-safe transcript and runtime state."""
        with self._ui_lock:
            messages = [dict(message) for message in self._ui_messages]
            revision = self._ui_revision
        return {
            "available": True,
            "running": self._running,
            "state": self.state,
            "last_info": self.last_info,
            "memory": self.memory.snapshot(),
            "diagnostics": self._diagnostics(),
            "revision": revision,
            "messages": messages,
        }

    def start_from_ui(self) -> dict:
        self.start()
        return self.get_ui_snapshot()

    def stop_from_ui(self) -> dict:
        self.stop()
        return self.get_ui_snapshot()

    def clear_from_ui(self) -> dict:
        cancelled = self.arbiter.interrupt_active_turn()
        if cancelled is not None:
            self.backend.cancel_turn(cancelled)
            self.coordinator.interrupt()
        self.backend.clear_history()
        self.memory.clear_context()
        self._pending_user_text.clear()
        with self._ui_lock:
            self._ui_messages.clear()
            self._ui_revision += 1
        self.last_info = "对话已清空"
        return self.get_ui_snapshot()

    def forget_from_ui(self) -> dict:
        """Explicitly remove the real-time path's local text memory and journals."""
        cancelled = self.arbiter.interrupt_active_turn()
        if cancelled is not None:
            self.backend.cancel_turn(cancelled)
            self.coordinator.interrupt()
        self.backend.clear_history()
        self.memory.forget_all()
        with self._ui_lock:
            self._ui_messages.clear()
            self._ui_revision += 1
        self._pending_user_text.clear()
        self.last_info = "本地记忆与日记已删除"
        return self.get_ui_snapshot()

    def _record_ui_event(self, event) -> None:
        """Coalesce streaming assistant tokens into one message per turn."""
        with self._ui_lock:
            if event.type == "transcript":
                self._ui_messages.append({
                    "turn_id": event.turn_id, "role": "user",
                    "text": str(event.payload.get("text", "")),
                    "final": bool(event.payload.get("final", False)),
                })
            elif event.type == "response_text":
                for message in reversed(self._ui_messages):
                    if message["turn_id"] == event.turn_id and message["role"] == "assistant":
                        message["text"] = str(event.payload.get("text", ""))
                        message["final"] = bool(event.payload.get("final", False))
                        break
                else:
                    self._ui_messages.append({
                        "turn_id": event.turn_id, "role": "assistant",
                        "text": str(event.payload.get("text", "")),
                        "final": bool(event.payload.get("final", False)),
                    })
            elif event.type == "error":
                self._ui_messages.append({
                    "turn_id": event.turn_id, "role": "system",
                    "text": "错误：" + str(event.payload.get("message", "未知错误")),
                    "final": True,
                })
            else:
                return
            self._ui_messages = self._ui_messages[-60:]
            self._ui_revision += 1

    def _start_welcome(self) -> None:
        text = self.config.welcome_message
        if not text:
            return
        self._welcome_generation += 1
        generation = self._welcome_generation
        self.state = "正在打招呼"
        self.last_info = "欢迎语"
        self._append_ui_message("assistant", text, "welcome")
        self._touch_ui()
        threading.Thread(
            target=self._play_welcome, args=(text, generation), daemon=True,
            name="realtime-welcome-tts",
        ).start()

    def _play_welcome(self, text: str, generation: int) -> None:
        try:
            pcm = self.backend.synthesize_text(text)
            if not pcm or not self._running or generation != self._welcome_generation:
                return
            handle = self.coordinator.on_dialogue_event(DialogueEvent(
                trace_id="welcome", turn_id=0, type="response_audio",
                created_at_ns=0,
                payload={
                    "pcm": pcm, "sample_rate": self.config.direct.tts_sample_rate,
                    "channels": 1, "sample_width": 2,
                },
            ))
            if hasattr(handle, "wait"):
                handle.wait()
            if self._running and generation == self._welcome_generation:
                self.state = "等待说话"
                self.last_info = "volc_direct"
                self._touch_ui()
        except Exception as exc:
            if self._running and generation == self._welcome_generation:
                self.state = "错误"
                self.last_info = f"欢迎语失败：{exc}"[:80]
                self._append_ui_message("system", self.last_info, "welcome-error")
                self._touch_ui()

    def _append_ui_message(self, role: str, text: str, turn_id: int | str) -> None:
        with self._ui_lock:
            self._ui_messages.append({"turn_id": turn_id, "role": role, "text": text, "final": True})
            self._ui_messages = self._ui_messages[-60:]
            self._ui_revision += 1

    def _diagnostics(self) -> dict:
        source = self.source.health()
        levels = source.get("recent_rms", [])
        return {
            "vad_threshold": source.get("effective_threshold", self.config.vad_threshold),
            "vad_floor": source.get("noise_floor", 0.0),
            "vad_calibrating": source.get("calibrating", False),
            "recent_rms": levels[-1] if levels else 0.0,
            "active_turn": source.get("active_turn"),
            "queue_depth": source.get("queue_depth", 0),
            "backend": self.backend.health(),
        }

    def _record_local_memory(self, event) -> None:
        if event.type == "transcript" and bool(event.payload.get("final", False)):
            self._pending_user_text[event.turn_id] = str(event.payload.get("text", ""))
            return
        if event.type == "error":
            self._pending_user_text.pop(event.turn_id, None)
            return
        if event.type != "response_text" or not bool(event.payload.get("final", False)):
            return
        user_text = self._pending_user_text.pop(event.turn_id, "")
        try:
            self.memory.record_completed_turn(
                event.trace_id, user_text, str(event.payload.get("text", ""))
            )
        except Exception as exc:
            # Conversation and playback must still complete if optional local
            # storage is temporarily unavailable.
            print(f"[REALTIME] 本地记忆写入失败：{exc}")

    def _touch_ui(self) -> None:
        with self._ui_lock:
            self._ui_revision += 1
