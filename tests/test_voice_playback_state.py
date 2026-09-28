import unittest

from voice.module import VoiceModule
from voice.backends.base import VoiceState


class _FakeHandle:
    error = None

    def wait(self, timeout=None):
        return True


class _FakeTTS:
    name = "fake-tts"

    def __init__(self):
        self.first_sample = None
        self.finished = None

    def set_first_sample_callback(self, callback):
        self.first_sample = callback

    def set_finished_callback(self, callback):
        self.finished = callback

    def speak(self, text, emotion=None):
        return _FakeHandle()


class VoicePlaybackStateTest(unittest.TestCase):
    def test_speaking_state_follows_first_audio_sample(self):
        states = []
        tts = _FakeTTS()
        voice = VoiceModule(
            asr=object(), llm=object(), tts=tts,
            state_callback=lambda state: states.append(state.state),
            enable_memory=False, enable_xp=False,
        )

        pending_ns = 1_000_000_000
        voice._set_state(VoiceState.TTS_PENDING, emotion="Joy", text="你好")
        voice._configure_playback_callbacks("Joy", "你好", pending_ns)
        self.assertIsInstance(voice._speak("你好", "Joy"), _FakeHandle)
        self.assertEqual(VoiceState.TTS_PENDING, voice.state.state)

        tts.first_sample(pending_ns + 20_000_000)
        self.assertEqual(VoiceState.SPEAKING, voice.state.state)
        self.assertEqual(1, voice.get_metrics()["playback_started_count"])
        self.assertEqual(20.0, voice.get_metrics()["tts_pending_to_first_sample_ms"]["p50"])

        tts.finished(pending_ns + 100_000_000, None)
        self.assertEqual(1, voice.get_metrics()["playback_finished_count"])
        self.assertIn(VoiceState.SPEAKING, states)


if __name__ == "__main__":
    unittest.main()
