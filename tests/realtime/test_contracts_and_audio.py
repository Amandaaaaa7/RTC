import unittest

import numpy as np

from voice.realtime.audio_format import Capture48kTo16k
from voice.realtime.contracts import Affect, SocialMotionIntent


class ContractsAndAudioTest(unittest.TestCase):
    def test_intent_expiry_is_derived_from_creation_time(self):
        intent = SocialMotionIntent(
            trace_id="trace", turn_id=1, kind="listen", target_person_id=None,
            created_at_ns=1_000, deadline_ms=2, expires_at_ns=2_001_000,
            commitment_level="orient", interruptibility="immediate",
            safety_class="passive", affect=Affect(), source="local_reflex",
        )
        self.assertFalse(intent.expired(2_001_000))
        self.assertTrue(intent.expired(2_001_001))
        with self.assertRaises(ValueError):
            SocialMotionIntent(
                trace_id="trace", turn_id=1, kind="listen", target_person_id=None,
                created_at_ns=1_000, deadline_ms=2, expires_at_ns=2_000,
                commitment_level="orient", interruptibility="immediate",
                safety_class="passive", affect=Affect(), source="local_reflex",
            )

    def test_exact_three_to_one_capture_conversion_keeps_residual(self):
        converter = Capture48kTo16k()
        frame = np.array([0, 65_536, 131_072, 196_608, 262_144], dtype=np.int64)
        first = np.frombuffer(converter.convert(frame), dtype="<i2")
        second = np.frombuffer(converter.convert(np.array([327_680], dtype=np.int64)), dtype="<i2")
        np.testing.assert_array_equal(first, np.array([1], dtype=np.int16))
        np.testing.assert_array_equal(second, np.array([4], dtype=np.int16))


if __name__ == "__main__":
    unittest.main()
