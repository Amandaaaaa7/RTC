import time
import unittest

from eye_display import EyeDisplay
from face_tracker import FaceTarget


class EyeSnapshotMetricsTest(unittest.TestCase):
    def setUp(self):
        self.eye = EyeDisplay()

    def test_fresh_latest_snapshot_updates_target_and_records_age(self):
        now_ns = time.monotonic_ns()
        snapshot = FaceTarget(0.25, -0.4, True, 0.9, None, now_ns - 25_000_000)

        target = self.eye._consume_face_snapshot(snapshot, now_s=10.0, now_ns=now_ns)

        self.assertEqual((0.25, -0.4), target)
        self.assertEqual(0, self.eye.snapshot_stale_drops)
        self.assertAlmostEqual(25.0, self.eye.snapshot_age_ms, places=3)
        self.assertEqual(0.25, self.eye._last_face_target_x)

    def test_stale_snapshot_cannot_replace_last_valid_target(self):
        self.eye._last_face_target_x = 0.1
        self.eye._last_face_target_y = 0.2
        now_ns = time.monotonic_ns()
        stale = FaceTarget(-0.8, 0.8, True, 0.9, None, now_ns - 2_000_000_000)

        target = self.eye._consume_face_snapshot(stale, now_s=20.0, now_ns=now_ns)

        self.assertIsNone(target)
        self.assertEqual(1, self.eye.snapshot_stale_drops)
        self.assertEqual(0.1, self.eye._last_face_target_x)
        self.assertEqual(0.2, self.eye._last_face_target_y)

    def test_face_entry_to_first_eye_frame_is_measured_once(self):
        now_ns = time.monotonic_ns()
        detected = FaceTarget(0.1, 0.2, True, 0.9, None, now_ns - 20_000_000)

        self.eye._consume_face_snapshot(detected, now_s=1.0, now_ns=now_ns)
        self.eye._record_eye_frame(now_ns + 20_000_000)
        self.eye._record_eye_frame(now_ns + 40_000_000)

        metric = self.eye.get_metrics()["face_to_eye_first_frame_ms"]
        self.assertEqual(1, metric["count"])
        self.assertAlmostEqual(40.0, metric["p50"], places=3)

    def test_fixed_policy_keeps_first_face_target(self):
        eye = EyeDisplay(gaze_policy="fixed")
        self.assertEqual((0.1, 0.2), eye._select_policy_target((0.1, 0.2)))
        self.assertEqual((0.1, 0.2), eye._select_policy_target((-0.6, 0.7)))

    def test_fixed_policy_lock_is_not_replaced_by_latest_hold_target(self):
        eye = EyeDisplay(gaze_policy="fixed")
        eye._select_policy_target((0.1, 0.2))
        # The latest snapshot/hold coordinates may advance after the first
        # lock, but fixed must still render the committed first target.
        eye._last_face_target_x = -0.6
        eye._last_face_target_y = 0.7
        self.assertEqual((0.1, 0.2), eye._fixed_target())

    def test_fixed_policy_is_identified_as_static_gaze_mode(self):
        eye = EyeDisplay(gaze_policy="fixed")
        self.assertEqual("fixed", eye.gaze_policy)

    def test_blink_latched_commits_latest_target_only_when_closed(self):
        eye = EyeDisplay(gaze_policy="blink_latched", retarget_deadband_x=0.01,
                         retarget_deadband_y=0.01)
        self.assertEqual((0.1, 0.2), eye._select_policy_target((0.1, 0.2), 100, 120, 1.0))
        self.assertEqual((0.1, 0.2), eye._select_policy_target((-0.6, 0.7), 200, 220, 2.0))
        eye._commit_blink_latched_target(300)
        self.assertEqual((-0.6, 0.7), (eye.target_x, eye.target_y))
        self.assertEqual((-0.6, 0.7), eye._pending_target)

    def test_blink_latched_ignores_jitter_and_only_keeps_one_pending_target(self):
        eye = EyeDisplay(gaze_policy="blink_latched", retarget_deadband_x=0.1,
                         retarget_deadband_y=0.1)
        eye._select_policy_target((0.0, 0.0), 100, 120, 1.0)
        eye._select_policy_target((0.05, 0.04), 200, 220, 2.0)
        self.assertEqual((0.0, 0.0), eye._pending_target)
        eye._select_policy_target((0.4, 0.0), 300, 320, 3.0)
        eye._select_policy_target((0.8, 0.0), 400, 420, 4.0)
        self.assertEqual((0.8, 0.0), eye._pending_target)
        self.assertEqual((0.0, 0.0), (eye._policy_target_x, eye._policy_target_y))

    def test_blink_latched_large_move_expedites_but_does_not_immediately_commit(self):
        eye = EyeDisplay(gaze_policy="blink_latched", retarget_deadband_x=0.01,
                         retarget_deadband_y=0.01, large_retarget_distance=0.3)
        eye._select_policy_target((0.0, 0.0), 100, 120, 10.0)
        eye._next_blink_time = 30.0
        eye._select_policy_target((0.8, 0.0), 200, 220, 10.0)
        self.assertGreaterEqual(eye._next_blink_time, 10.3)
        self.assertLessEqual(eye._next_blink_time, 10.7)
        self.assertEqual((0.0, 0.0), (eye._policy_target_x, eye._policy_target_y))

    def test_policy_timing_separates_commit_from_first_eye_frame(self):
        eye = EyeDisplay(gaze_policy="blink_latched")
        eye._select_policy_target((0.2, 0.3), 1_000_000, 2_000_000, 1.0)
        eye._record_eye_frame(5_000_000)
        metrics = eye.get_metrics()
        self.assertAlmostEqual(1.0, metrics["snapshot_to_policy_commit_ms"]["p50"])
        self.assertAlmostEqual(3.0, metrics["policy_commit_to_first_eye_frame_ms"]["p50"])


if __name__ == "__main__":
    unittest.main()
