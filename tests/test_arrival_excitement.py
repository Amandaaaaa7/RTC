import unittest

from eye_display import ARRIVAL_REARM_AFTER_NO_FACE_S, EyeDisplay
from eye_expressions import ArrivalExcitement
from face_tracker import FaceTarget


class ArrivalExcitementTests(unittest.TestCase):
    def test_generator_is_short_and_preserves_gaze(self):
        cue = ArrivalExcitement()
        first = cue.step(0.0)
        peak = cue.step(0.4)

        self.assertGreater(cue.duration, 0.8)
        self.assertLessEqual(cue.duration, 1.0)
        self.assertTrue(cue.preserves_gaze)
        self.assertNotEqual(peak["pupil_relative_scale"], first["pupil_relative_scale"])
        self.assertIn("eyelid_tilt", peak)
        self.assertIn("iris_scale", peak)

    def test_first_face_triggers_once_until_sustained_absence(self):
        eye = EyeDisplay()
        detected = FaceTarget(0.3, -0.2, True, 0.9, None, 1, snapshot_at_ns=1)
        absent = FaceTarget(0.3, -0.2, False, 0.0, None, 2, snapshot_at_ns=2)

        eye._consume_face_snapshot(detected, now_s=10.0, now_ns=1)
        self.assertIsNotNone(eye._arrival_excitement_for_frame(10.1))
        self.assertFalse(eye._arrival_excitement_armed)

        # A single missed detector result does not re-arm or replay the cue.
        eye._consume_face_snapshot(absent, now_s=10.2, now_ns=2)
        eye._consume_face_snapshot(detected, now_s=10.3, now_ns=1)
        self.assertEqual(eye._arrival_excitement_start, 10.0)

        # A real absence re-arms the next no-face -> face transition.
        eye._consume_face_snapshot(absent, now_s=20.0, now_ns=2)
        eye._consume_face_snapshot(
            absent,
            now_s=20.0 + ARRIVAL_REARM_AFTER_NO_FACE_S,
            now_ns=2,
        )
        eye._consume_face_snapshot(detected, now_s=22.0, now_ns=1)
        self.assertEqual(eye._arrival_excitement_start, 22.0)


if __name__ == "__main__":
    unittest.main()
