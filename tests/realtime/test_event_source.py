import time
import unittest

import numpy as np

from voice.realtime.event_source import VoiceEventSource


class FakeMic:
    def __init__(self):
        self.callbacks = []

    def register_audio_callback(self, callback):
        self.callbacks.append(callback)

    def unregister_audio_callback(self, callback):
        self.callbacks.remove(callback)

    def feed(self, frame):
        for callback in list(self.callbacks):
            callback(frame)


class VoiceEventSourceTest(unittest.TestCase):
    def test_onset_audio_and_end_are_ordered(self):
        mic = FakeMic()
        events, audio_turns = [], []
        source = VoiceEventSource(
            mic, events.append, lambda turn_id, _frame: audio_turns.append(turn_id),
            threshold=0.1, silence_duration_s=0.4, sample_rate=10, block_size=2,
            calibration_frames=0,
        )
        source.start()
        loud = np.full(2, 500_000_000, dtype=np.int32)
        quiet = np.zeros(2, dtype=np.int32)
        mic.feed(loud)
        mic.feed(quiet)
        mic.feed(quiet)
        deadline = time.time() + 1.0
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.01)
        source.stop()
        self.assertEqual(["voice_onset", "voice_end"], [event.type for event in events])
        self.assertEqual([1, 1, 1], audio_turns)

    def test_room_noise_is_calibrated_above_configured_floor_and_still_releases_turn(self):
        mic = FakeMic()
        events = []
        source = VoiceEventSource(
            mic, events.append, lambda _turn_id, _frame: None,
            threshold=0.005, silence_duration_s=0.4, sample_rate=10, block_size=2,
            calibration_frames=2,
        )
        source.start()
        room_noise = np.full(2, int(0.016 * 2_147_483_647), dtype=np.int32)
        speech = np.full(2, int(0.050 * 2_147_483_647), dtype=np.int32)
        mic.feed(room_noise)
        mic.feed(room_noise)
        mic.feed(speech)
        mic.feed(room_noise)
        mic.feed(room_noise)
        deadline = time.time() + 1.0
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.01)
        health = source.health()
        source.stop()
        self.assertEqual(["voice_onset", "voice_end"], [event.type for event in events])
        self.assertGreater(health["effective_threshold"], 0.016)
        self.assertLess(room_noise[0] / 2_147_483_647.0, health["release_threshold"])


if __name__ == "__main__":
    unittest.main()
