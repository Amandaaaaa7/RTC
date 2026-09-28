import unittest

from voice.realtime.contracts import DialogueEvent, VoiceEvent
from voice.realtime.response_arbiter import ResponseArbiter


def voice_event(turn_id, trace_id, event_type):
    return VoiceEvent(trace_id, turn_id, turn_id, event_type, 100, {})


class ResponseArbiterTest(unittest.TestCase):
    def test_new_onset_cancels_old_turn_and_drops_late_audio(self):
        arbiter = ResponseArbiter()
        first = arbiter.on_voice_event(voice_event(1, "a", "voice_onset"), [])
        self.assertTrue(first.accepted)
        second = arbiter.on_voice_event(voice_event(2, "b", "voice_onset"), [])
        self.assertTrue(second.accepted)
        self.assertEqual(1, second.cancel_turn_id)

        old_audio = DialogueEvent("a", 1, "response_audio", 101, {"pcm": b"x"})
        new_audio = DialogueEvent("b", 2, "response_audio", 102, {"pcm": b"x"})
        self.assertFalse(arbiter.on_dialogue_event(old_audio).accepted)
        self.assertTrue(arbiter.on_dialogue_event(new_audio).accepted)

    def test_unrelated_voice_end_is_rejected(self):
        arbiter = ResponseArbiter()
        arbiter.on_voice_event(voice_event(2, "b", "voice_onset"), [])
        self.assertFalse(arbiter.on_voice_event(voice_event(1, "a", "voice_end"), []).accepted)


if __name__ == "__main__":
    unittest.main()
