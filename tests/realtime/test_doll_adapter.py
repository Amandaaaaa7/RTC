import time
import unittest

from voice.realtime.contracts import Affect, SocialMotionIntent
from voice.realtime.embodiment import DollAdapter


class FakeEye:
    def __init__(self):
        self.states = []

    def on_voice_state(self, state):
        self.states.append(state.state)


class FakeSpeaker:
    def __init__(self):
        self.calls = []

    def play_buffer(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "handle"

    def get_playback_metrics(self):
        return {"state": "idle"}


class FakeHandle:
    def wait(self, timeout=None):
        return True


class FakeMic:
    def __init__(self):
        self.paused = 0
        self.resumed = 0
        self.drained = 0

    def pause_callbacks(self):
        self.paused += 1

    def resume_callbacks(self):
        self.resumed += 1

    def drain_all_queues(self):
        self.drained += 1


class DollAdapterTest(unittest.TestCase):
    def test_only_passive_non_expired_intents_reach_existing_eye_api(self):
        eye, speaker = FakeEye(), FakeSpeaker()
        adapter = DollAdapter(eye, speaker)
        created_at_ns = time.monotonic_ns()
        intent = SocialMotionIntent(
            trace_id="t", turn_id=1, kind="listen", target_person_id=None,
            created_at_ns=created_at_ns, deadline_ms=10_000,
            expires_at_ns=created_at_ns + 10_000 * 1_000_000,
            commitment_level="orient", interruptibility="immediate",
            safety_class="passive", affect=Affect(), source="local_reflex",
        )
        self.assertTrue(adapter.execute(intent))
        self.assertEqual(["listening"], eye.states)
        self.assertEqual("handle", adapter.play_pcm(b"pcm", 24_000, 1, 2))

    def test_playback_echo_guard_pauses_only_the_realtime_callback_path(self):
        eye, speaker, mic = FakeEye(), FakeSpeaker(), FakeMic()
        speaker.play_buffer = lambda *args, **kwargs: FakeHandle()
        adapter = DollAdapter(eye, speaker, mic_monitor=mic)
        adapter.play_pcm(b"pcm", 24_000, 1, 2)
        deadline = time.monotonic() + 1.0
        while not mic.resumed and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(1, mic.paused)
        self.assertEqual(1, mic.drained)
        self.assertEqual(1, mic.resumed)


if __name__ == "__main__":
    unittest.main()
