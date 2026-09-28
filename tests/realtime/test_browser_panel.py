import time
import unittest
import json
import tempfile
import threading
from pathlib import Path

from voice.realtime.contracts import DialogueEvent, VoiceEvent
from voice.realtime.orchestrator import RealtimeVoiceOrchestrator


class FakeMic:
    def pause_callbacks(self):
        pass

    def resume_callbacks(self):
        pass

    def drain_all_queues(self):
        pass


class FakeSpeaker:
    def get_playback_metrics(self):
        return {"state": "idle"}


class FakeCoordinator:
    def __init__(self):
        self.audio_seen = threading.Event()

    def on_dialogue_event(self, event):
        if event.type == "response_audio":
            self.audio_seen.set()
        return None


class BrowserPanelContractTest(unittest.TestCase):
    def test_transcript_and_streaming_response_are_exposed_and_clearable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "realtime.json"
            config_path.write_text(json.dumps({
                "local_memory": {
                    "enabled": True, "max_context_turns": 3,
                    "store_path": str(root / "memory.json"),
                    "journal_dir": str(root / "journal"),
                }
            }), encoding="utf-8")
            orchestrator = RealtimeVoiceOrchestrator(
                FakeMic(), config_path=str(config_path), speaker_module=FakeSpeaker()
            )
            now = time.monotonic_ns()
            onset = VoiceEvent("trace-1", 1, 1, "voice_onset", now)
            orchestrator.arbiter.on_voice_event(onset, [])

            orchestrator._on_dialogue_event(DialogueEvent(
                "trace-1", 1, "transcript", now, {"text": "你好", "final": True}
            ))
            orchestrator._on_dialogue_event(DialogueEvent(
                "trace-1", 1, "response_text", now, {"text": "你好呀", "final": False}
            ))
            orchestrator._on_dialogue_event(DialogueEvent(
                "trace-1", 1, "response_text", now, {"text": "你好呀！", "final": True}
            ))

            snapshot = orchestrator.get_ui_snapshot()
            self.assertTrue(snapshot["available"])
            self.assertEqual(["你好", "你好呀！"], [item["text"] for item in snapshot["messages"]])
            self.assertEqual(1, snapshot["memory"]["stored_turns"])
            self.assertGreater(snapshot["revision"], 0)

            cleared = orchestrator.clear_from_ui()
            self.assertEqual([], cleared["messages"])
            self.assertEqual("对话已清空", cleared["last_info"])
            self.assertEqual(0, cleared["memory"]["stored_turns"])

    def test_startup_welcome_is_shown_and_sent_to_existing_audio_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "realtime.json"
            config_path.write_text(json.dumps({"welcome_message": "你好，我是圆宝"}), encoding="utf-8")
            orchestrator = RealtimeVoiceOrchestrator(
                FakeMic(), config_path=str(config_path), speaker_module=FakeSpeaker()
            )
            coordinator = FakeCoordinator()
            orchestrator.coordinator = coordinator
            orchestrator.backend.synthesize_text = lambda text: b"pcm"  # type: ignore[method-assign]
            orchestrator._running = True

            orchestrator._start_welcome()
            self.assertTrue(coordinator.audio_seen.wait(timeout=1.0))
            self.assertEqual("你好，我是圆宝", orchestrator.get_ui_snapshot()["messages"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
