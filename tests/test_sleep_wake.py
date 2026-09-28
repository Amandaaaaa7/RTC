import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eye_expressions import SleepExpression, get_expression, list_expressions
from eye_display import EyeDisplay


class FakeTracker:
    """FaceTracker 的最小替身：只提供睡/醒状态机读取的两个方法。"""

    def __init__(self, absent=None, present=None, running=True, error=None):
        self._absent = absent
        self._present = present
        self.running = running
        self.error_message = error

    def face_absent_duration(self):
        return self._absent

    def face_present_duration(self):
        return self._present


class SleepExpressionTest(unittest.TestCase):
    def test_registered_in_expressions(self):
        self.assertIs(get_expression("sleep"), get_expression("sleep"))
        names = [n for n, _ in list_expressions()]
        self.assertIn("sleep", names)

    def test_soften_phase_partially_closes(self):
        expr = SleepExpression()
        params = expr.step(1.0)  # SOFTEN midpoint
        self.assertGreater(params["eyelid"], 0)
        self.assertLess(params["eyelid"], 110)

    def test_blink_phase_holds_eyelid(self):
        expr = SleepExpression()
        params = expr.step(7.0)  # BLINK phase
        self.assertEqual(params["eyelid"], 110)

    def test_fully_closed_by_20s(self):
        expr = SleepExpression()
        params = expr.step(20.0)
        self.assertEqual(params["eyelid"], 110)
        self.assertEqual(params["eyelid_target_y"], 131.0)

    def test_awake_resets(self):
        expr = SleepExpression()
        expr.step(10.0)
        expr.awake()
        self.assertFalse(expr.loop)


class SleepStateMachineTest(unittest.TestCase):
    def _make_eye(self, tracker, timeout=120.0, confirm=0.15):
        return EyeDisplay(
            face_tracker=tracker,
            sleep_on_absence=True,
            sleep_absence_timeout=timeout,
            sleep_wake_confirm=confirm,
        )

    def test_disabled_by_default_no_takeover(self):
        eye = EyeDisplay(face_tracker=FakeTracker(absent=200.0))
        self.assertFalse(eye.sleep_on_absence)
        self.assertFalse(eye._update_sleep_state(0.0))

    def test_awake_when_face_visible(self):
        eye = self._make_eye(FakeTracker(absent=None, present=1.0))
        self.assertFalse(eye._update_sleep_state(0.0))
        self.assertEqual(eye._sleep_state, "awake")

    def test_falling_after_timeout(self):
        eye = self._make_eye(FakeTracker(absent=120.0, present=None))
        self.assertTrue(eye._update_sleep_state(0.0))
        self.assertEqual(eye._sleep_state, "falling")
        self.assertIsNotNone(eye._sleep_expression)

    def test_falling_wakes_immediately_on_face(self):
        tracker = FakeTracker(absent=120.0, present=None)
        eye = self._make_eye(tracker)
        self.assertTrue(eye._update_sleep_state(0.0))  # falling
        tracker._absent = None
        tracker._present = 0.01  # face appears
        self.assertFalse(eye._update_sleep_state(0.5))  # face -> wake
        self.assertEqual(eye._sleep_state, "awake")
        self.assertIsNone(eye._sleep_expression)

    def test_asleep_requires_confirm_to_wake(self):
        tracker = FakeTracker(absent=None, present=0.05)
        eye = self._make_eye(tracker, confirm=0.15)
        eye._sleep_state = "asleep"
        # 去抖窗口内仍保持睡着
        self.assertTrue(eye._update_sleep_state(0.0))
        self.assertEqual(eye._sleep_state, "asleep")
        # 超过确认时长后唤醒
        self.assertFalse(eye._update_sleep_state(0.2))
        self.assertEqual(eye._sleep_state, "awake")

    def test_absent_below_timeout_stays_awake(self):
        eye = self._make_eye(FakeTracker(absent=119.0, present=None))
        self.assertFalse(eye._update_sleep_state(0.0))
        self.assertEqual(eye._sleep_state, "awake")


if __name__ == "__main__":
    unittest.main()
