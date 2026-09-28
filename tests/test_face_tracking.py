import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import face_tracker as tracker_module
from face_detectors import FaceDetection, _largest_by_area
from face_tracker import FaceTracker, GazeCalibration


class FakeDetector:
    name = "fake"

    def __init__(self, detection_or_list):
        self._detections = detection_or_list

    def detect(self, _frame):
        return self.detect_multi(_frame, largest_only=True)

    def detect_multi(self, _frame, largest_only=False):
        if self._detections is None:
            return [] if not largest_only else None
        if isinstance(self._detections, list):
            if largest_only:
                return _largest_by_area(self._detections)
            return self._detections
        # single detection
        if largest_only:
            return self._detections
        return [self._detections]


class FakeCv2:
    IMREAD_COLOR = 1

    @staticmethod
    def imdecode(_data, _mode):
        return np.zeros((100, 200, 3), dtype=np.uint8)


class GazeCalibrationTest(unittest.TestCase):
    def test_loads_day1_config(self):
        calibration = GazeCalibration.from_json(
            Path(__file__).resolve().parent.parent / "config" / "gaze_calibration.json"
        )
        self.assertEqual((calibration.x_sign, calibration.y_sign), (1.0, 1.0))
        self.assertEqual((calibration.deadband_x, calibration.deadband_y), (0.05, 0.05))
        self.assertEqual(calibration.smoothing, 0.35)

    def test_maps_sign_gain_bias_deadband_and_clamps(self):
        calibration = GazeCalibration(
            x_sign=-1,
            y_sign=1,
            x_gain=2,
            y_gain=0.5,
            x_bias=0.1,
            y_bias=-0.1,
            deadband_x=0.05,
            deadband_y=0.05,
        )
        self.assertEqual(calibration.map(0.75, 0.4), (-1.0, 0.1))
        self.assertEqual(calibration.map(0.05, 0.2), (0.0, 0.0))


class FaceTrackerTest(unittest.TestCase):
    def setUp(self):
        self.original_cv2 = tracker_module.cv2
        tracker_module.cv2 = FakeCv2()

    def tearDown(self):
        tracker_module.cv2 = self.original_cv2

    def test_process_jpeg_normalizes_and_calibrates_center(self):
        detection = FaceDetection(
            box_xywh=(140, 10, 40, 20),
            center_x=160,
            center_y=20,
            confidence=0.9,
            inferred_at_ns=123,
        )
        tracker = FaceTracker(
            detector=FakeDetector(detection),
            calibration=GazeCalibration(x_sign=-1, y_gain=0.5),
        )
        target = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target.detected)
        self.assertAlmostEqual(target.x, -0.6)
        self.assertAlmostEqual(target.y, -0.3)
        self.assertEqual(target.confidence, 0.9)
        self.assertEqual(target.box_xywh, (140, 10, 40, 20))
        self.assertAlmostEqual(target.camera_x, 0.6)
        self.assertAlmostEqual(target.camera_y, -0.6)

    def test_no_detection_keeps_position_but_marks_not_detected(self):
        tracker = FaceTracker(detector=FakeDetector(None))
        tracker._update_target(
            tracker_module.FaceTarget(0.4, -0.2, True, 0.8, (1, 2, 3, 4), 10)
        )
        target = tracker.process_jpeg(b"jpeg")
        self.assertEqual((target.x, target.y), (0.4, -0.2))
        self.assertFalse(target.detected)

    def test_lock_persists_when_another_face_is_only_slightly_larger(self):
        """M1: small area lead should not cause a switch."""
        locked = FaceDetection(
            box_xywh=(80, 20, 40, 40), center_x=100, center_y=40,
            confidence=0.9, inferred_at_ns=1
        )
        slightly_larger = FaceDetection(
            box_xywh=(140, 20, 44, 44), center_x=162, center_y=42,
            confidence=0.9, inferred_at_ns=2
        )
        tracker = FaceTracker(
            detector=FakeDetector([locked]),
            target_hold_timeout=0.8,
            switch_area_ratio=1.5,
            iou_threshold=0.3,
        )
        # First frame: lock onto the only face.
        tracker.process_jpeg(b"jpeg")
        # Second frame: two faces, the slightly larger one is not 1.5x larger.
        tracker._detector = FakeDetector([locked, slightly_larger])
        target = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target.detected)
        # The lock should keep the original center (100) rather than jump.
        self.assertAlmostEqual(target.camera_x, 100 / 200 * 2.0 - 1.0)
        self.assertEqual(tracker._target_switch_count, 0)

    def test_switch_to_significantly_larger_face_when_lock_is_lost(self):
        """M2: in multi-face scene, a clearly larger face can take over after hold timeout."""
        old = FaceDetection(
            box_xywh=(80, 20, 40, 40), center_x=100, center_y=40,
            confidence=0.9, inferred_at_ns=1
        )
        # The old face moves far away / gets small (no IOU match), while a new
        # much larger face appears. A third face stays in the scene as context.
        new_big = FaceDetection(
            box_xywh=(140, 20, 80, 80), center_x=180, center_y=60,
            confidence=0.9, inferred_at_ns=2
        )
        other = FaceDetection(
            box_xywh=(10, 70, 30, 30), center_x=25, center_y=85,
            confidence=0.9, inferred_at_ns=2
        )
        tracker = FaceTracker(
            detector=FakeDetector([old, other]),
            target_hold_timeout=0.0,  # no hold delay for this test
            switch_area_ratio=1.5,
            iou_threshold=0.3,
        )
        # First frame locks onto the largest face (old).
        tracker.process_jpeg(b"jpeg")
        # Second frame: old is gone; new_big is present with another face.
        tracker._detector = FakeDetector([new_big, other])
        target = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target.detected)
        self.assertAlmostEqual(target.camera_x, 180 / 200 * 2.0 - 1.0)
        self.assertEqual(tracker._target_switch_count, 1)

    def test_dominant_face_override_while_locked_target_still_visible(self):
        """M4: another face becomes significantly larger for consecutive frames."""
        old = FaceDetection(
            box_xywh=(80, 20, 40, 40), center_x=100, center_y=40,
            confidence=0.9, inferred_at_ns=1
        )
        new_big = FaceDetection(
            box_xywh=(140, 20, 80, 80), center_x=180, center_y=60,
            confidence=0.9, inferred_at_ns=2
        )
        tracker = FaceTracker(
            detector=FakeDetector([old]),
            target_hold_timeout=0.3,
            switch_area_ratio=1.3,
            iou_threshold=0.3,
            dominant_switch_frames=2,
        )
        tracker.process_jpeg(b"jpeg")  # lock old
        # First frame where new_big appears alongside old.
        tracker._detector = FakeDetector([old, new_big])
        target1 = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target1.detected)
        self.assertAlmostEqual(target1.camera_x, 100 / 200 * 2.0 - 1.0)
        self.assertEqual(tracker._target_switch_count, 0)
        # Second consecutive frame where new_big is dominant -> switch.
        tracker._detector = FakeDetector([old, new_big])
        target2 = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target2.detected)
        self.assertAlmostEqual(target2.camera_x, 180 / 200 * 2.0 - 1.0)
        self.assertEqual(tracker._target_switch_count, 1)

    def test_multi_to_single_face_locks_immediately(self):
        """M5: when only one face remains, lock it immediately."""
        old = FaceDetection(
            box_xywh=(80, 20, 40, 40), center_x=100, center_y=40,
            confidence=0.9, inferred_at_ns=1
        )
        other = FaceDetection(
            box_xywh=(140, 20, 50, 50), center_x=165, center_y=45,
            confidence=0.9, inferred_at_ns=2
        )
        tracker = FaceTracker(
            detector=FakeDetector([old, other]),
            target_hold_timeout=0.3,
            switch_area_ratio=1.3,
            iou_threshold=0.3,
        )
        tracker.process_jpeg(b"jpeg")  # lock old
        tracker._detector = FakeDetector([other])
        target = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target.detected)
        self.assertAlmostEqual(target.camera_x, 165 / 200 * 2.0 - 1.0)

    def test_lost_lock_falls_back_to_largest_without_ratio(self):
        """M6: locked target lost, remaining faces are not ratio-times larger."""
        old = FaceDetection(
            box_xywh=(80, 20, 80, 80), center_x=120, center_y=50,
            confidence=0.9, inferred_at_ns=1
        )
        remaining = FaceDetection(
            box_xywh=(140, 20, 90, 90), center_x=185, center_y=55,
            confidence=0.9, inferred_at_ns=2
        )
        extra = FaceDetection(
            box_xywh=(10, 70, 20, 20), center_x=20, center_y=80,
            confidence=0.9, inferred_at_ns=2
        )
        tracker = FaceTracker(
            detector=FakeDetector([old]),
            target_hold_timeout=0.0,  # immediate fallback
            switch_area_ratio=2.0,      # remaining is NOT 2x larger than old
            iou_threshold=0.3,
        )
        tracker.process_jpeg(b"jpeg")  # lock old
        # Old disappears; remaining + extra are present. Even though remaining
        # is not 2x larger than old, it is the largest face, so we should fall
        # back to it immediately.
        tracker._detector = FakeDetector([remaining, extra])
        target = tracker.process_jpeg(b"jpeg")
        self.assertTrue(target.detected)
        self.assertAlmostEqual(target.camera_x, 185 / 200 * 2.0 - 1.0)
        self.assertEqual(tracker._target_switch_count, 1)


class DetectorSelectionTest(unittest.TestCase):
    def test_largest_face_is_selected_by_area(self):
        small = FaceDetection((0, 0, 10, 10), 5, 5, 0.99, 1)
        large = FaceDetection((0, 0, 20, 20), 10, 10, 0.8, 2)
        self.assertIs(_largest_by_area([small, large]), large)


class FaceAbsenceDurationTest(unittest.TestCase):
    """face_absent_duration / face_present_duration 作为入睡计时起点。"""

    def setUp(self):
        self.original_cv2 = tracker_module.cv2
        tracker_module.cv2 = FakeCv2()
        # 冻结时钟：tracker 经 clock 模块读时间，替换其属性即可注入假时钟
        # （time.monotonic/monotonic_ns 是 C 函数，无法安全替换）。
        self._clock = tracker_module.clock
        self.original_monotonic = self._clock.monotonic
        self.original_monotonic_ns = self._clock.monotonic_ns
        self._now_ns = 1_000_000_000_000
        self._clock.monotonic = lambda: self._now_ns / 1_000_000_000.0
        self._clock.monotonic_ns = lambda: self._now_ns

    def tearDown(self):
        tracker_module.cv2 = self.original_cv2
        self._clock.monotonic = self.original_monotonic
        self._clock.monotonic_ns = self.original_monotonic_ns

    def _advance(self, seconds):
        self._now_ns += int(seconds * 1e9)

    def test_absent_none_when_face_visible(self):
        detection = FaceDetection(
            box_xywh=(140, 10, 40, 20), center_x=160, center_y=20,
            confidence=0.9, inferred_at_ns=1,
        )
        tracker = FaceTracker(detector=FakeDetector(detection), target_hold_timeout=0.0)
        tracker.process_jpeg(b"jpeg")
        self.assertIsNone(tracker.face_absent_duration())
        self.assertGreaterEqual(tracker.face_present_duration(), 0.0)

    def test_absent_grows_after_lock_expires(self):
        detection = FaceDetection(
            box_xywh=(140, 10, 40, 20), center_x=160, center_y=20,
            confidence=0.9, inferred_at_ns=1,
        )
        tracker = FaceTracker(detector=FakeDetector(detection), target_hold_timeout=0.3)
        tracker.process_jpeg(b"jpeg")  # lock onto the face
        self.assertIsNone(tracker.face_absent_duration())

        self._advance(0.1)  # 仍在 hold timeout 内，锁定保持
        tracker._detector = FakeDetector(None)  # face disappears
        tracker.process_jpeg(b"jpeg")  # within hold timeout -> still locked
        # 首次 no-face 帧会设定 _lost_since_ns，但锁定尚未过期，仍视为“有脸保持”
        self.assertTrue(tracker._locked_detection is not None)

        self._advance(0.5)  # beyond hold timeout -> lock expires
        tracker.process_jpeg(b"jpeg")
        # 锁定已过期，但无脸时长从锁定最后时刻起算，已开始累计
        absent = tracker.face_absent_duration()
        self.assertIsNotNone(absent)
        self.assertGreaterEqual(absent, 0.3)

        # 继续累计：无脸时长增长
        self._advance(0.1)
        tracker.process_jpeg(b"jpeg")
        self.assertGreater(tracker.face_absent_duration(), absent)

    def test_absent_resets_when_face_returns(self):
        detection = FaceDetection(
            box_xywh=(140, 10, 40, 20), center_x=160, center_y=20,
            confidence=0.9, inferred_at_ns=1,
        )
        tracker = FaceTracker(detector=FakeDetector(None), target_hold_timeout=0.0)
        tracker.process_jpeg(b"jpeg")  # no face at all -> absent is None (never locked)
        self.assertIsNone(tracker.face_absent_duration())

        tracker._detector = FakeDetector(detection)
        tracker.process_jpeg(b"jpeg")  # face appears -> lock
        self.assertIsNone(tracker.face_absent_duration())
        self.assertIsNotNone(tracker.face_present_duration())
