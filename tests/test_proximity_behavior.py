import unittest

from eye_display import INTEREST_FINAL_HOLD_S, EyeDisplay
from eye_expressions import JsonAnchorExpression
from face_tracker import FaceTarget
from proximity_behavior import ProximityBehavior


class ProximityBehaviorTests(unittest.TestCase):
    def test_near_entry_then_sustained_far_distance_disengages(self):
        behavior = ProximityBehavior()
        behavior.observe(0.02, 0.0)
        approach = behavior.observe(0.30, 0.5)
        self.assertEqual(approach.state, "approaching")
        self.assertTrue(approach.approach_started)

        updates = []
        for now_s in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
            updates.append(behavior.observe(0.001, now_s))
        self.assertEqual(behavior.state, "disengaging")
        self.assertTrue(any(update.started_disengaging for update in updates))

        behavior.finish_disengaging()
        self.assertEqual(behavior.state, "distant")

    def test_interest_expression_uses_exact_json_endpoints(self):
        interest = JsonAnchorExpression("Interest", 1.0)

        self.assertEqual(interest.step(0.0), interest._frames[0]["params"])
        self.assertEqual(
            interest.step(interest.duration), interest._frames[-1]["params"]
        )
        self.assertTrue(interest.preserves_gaze)

    def test_slow_approach_uses_the_fallback_boundary_once(self):
        behavior = ProximityBehavior()
        behavior.observe(0.02, 0.0)
        updates = []
        for now_s, area in enumerate(
                (0.024, 0.027, 0.030, 0.034, 0.038, 0.043, 0.048,
                 0.054, 0.060, 0.067), start=1):
            updates.append(behavior.observe(area, float(now_s)))

        self.assertEqual(sum(update.approach_started for update in updates), 1)
        self.assertTrue(updates[-1].approach_started)

    def test_retreat_cancels_active_interest_hold(self):
        eye = EyeDisplay()

        def snapshot(area, sequence):
            return FaceTarget(0.2, 0.1, True, 0.9, None, sequence,
                              snapshot_at_ns=sequence, face_area_ratio=area)

        eye._consume_face_snapshot(snapshot(0.30, 1), now_s=0.0, now_ns=1)
        eye._interest_start = 0.0
        for now_s, sequence in ((0.5, 2), (1.0, 3), (1.5, 4)):
            eye._consume_face_snapshot(snapshot(0.001, sequence), now_s, sequence)

        self.assertEqual(eye.proximity_state, "retreating")
        self.assertIsNone(eye._interest_start)

    def test_interest_holds_final_frame_for_three_seconds_then_clears(self):
        eye = EyeDisplay()
        eye._interest_start = 10.0
        duration = eye._interest_expression.duration

        self.assertIsNotNone(eye._interest_for_frame(10.0 + duration + 2.9))
        self.assertIsNone(
            eye._interest_for_frame(10.0 + duration + INTEREST_FINAL_HOLD_S)
        )


if __name__ == "__main__":
    unittest.main()
