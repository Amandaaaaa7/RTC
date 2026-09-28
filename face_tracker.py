"""Latest-value face tracking for CameraStreamer and EyeDisplay."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
import time

import numpy as np

from device_log import device_log
from face_detectors import create_face_detector
import clock

try:
    import cv2
except ImportError:  # Keep imports/test discovery working off the Pi.
    cv2 = None


@dataclass(frozen=True)
class FaceTarget:
    x: float
    y: float
    detected: bool
    confidence: float
    box_xywh: tuple[int, int, int, int] | None
    inferred_at_ns: int
    # Raw, pre-mapping values used only to record a calibration sample.
    camera_x: float | None = None
    camera_y: float | None = None
    # Time of the current no-face -> face transition.  This is metadata on the
    # one latest snapshot, not a queue of historical coordinates.
    face_entered_at_ns: int = 0
    # Day 3 timing: latest JPEG 组装完成时刻，以及 FaceTracker 发布该唯一快照的时刻。
    captured_at_ns: int = 0
    snapshot_at_ns: int = 0
    # Face box area divided by frame area: a resolution-independent distance
    # proxy used by the eye interaction state machine.
    face_area_ratio: float = 0.0


@dataclass(frozen=True)
class GazeCalibration:
    """Camera-normalized coordinates -> EyeDisplay target coordinates."""

    x_sign: float = 1.0
    y_sign: float = 1.0
    x_gain: float = 1.0
    y_gain: float = 1.0
    x_bias: float = 0.0
    y_bias: float = 0.0
    deadband_x: float = 0.0
    deadband_y: float = 0.0
    # Applied only by EyeDisplay during continuous testing.
    smoothing: float = 0.35
    # Day 3 blink-latched policy: ignore tiny target deltas and expedite only
    # clearly meaningful retargets.  These are policy hysteresis values, not
    # the Day 1 centre-offset deadband above.
    retarget_deadband_x: float = 0.06
    retarget_deadband_y: float = 0.06
    large_retarget_distance: float = 0.35

    @classmethod
    def from_json(cls, path: str | Path) -> "GazeCalibration":
        # Load the single reproducible Day 1 calibration record.
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("gaze calibration must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown gaze calibration fields: {sorted(unknown)}")
        return cls(**values)

    def with_overrides(self, **values) -> "GazeCalibration":
        # Apply only explicitly supplied command-line A/B overrides.
        return replace(self, **{
            key: value for key, value in values.items() if value is not None
        })

    def map(self, camera_x: float, camera_y: float) -> tuple[float, float]:
        eye_x = camera_x * self.x_sign * self.x_gain + self.x_bias
        eye_y = camera_y * self.y_sign * self.y_gain + self.y_bias
        if abs(eye_x) < self.deadband_x:
            eye_x = 0.0
        if abs(eye_y) < self.deadband_y:
            eye_y = 0.0
        return (
            max(-1.0, min(1.0, eye_x)),
            max(-1.0, min(1.0, eye_y)),
        )


class FaceTracker:
    """Continuously detect the largest face and expose only the latest target."""

    def __init__(
        self,
        camera_streamer=None,
        fps: float = 2.0,
        detector_name: str = "yunet",
        confidence_threshold: float = 0.7,
        calibration: GazeCalibration | None = None,
        detector=None,
        target_hold_timeout: float = 0.3,
        switch_area_ratio: float = 1.3,
        iou_threshold: float = 0.3,
        dominant_switch_frames: int = 2,
    ):
        if fps <= 0:
            raise ValueError("fps 必须大于 0")
        self.camera = camera_streamer
        self.fps = fps
        self.detector_name = detector_name
        self.confidence_threshold = confidence_threshold
        self.calibration = calibration or GazeCalibration()
        self._detector = detector

        # Multi-face stabilization parameters.
        self.target_hold_timeout = max(0.0, target_hold_timeout)
        self.switch_area_ratio = max(1.0, switch_area_ratio)
        self.iou_threshold = max(0.0, min(1.0, iou_threshold))
        self.dominant_switch_frames = max(1, dominant_switch_frames)

        self.running = False
        self._thread = None
        self._lock = threading.Lock()
        self._target = FaceTarget(0.0, 0.0, False, 0.0, None, 0)
        self.last_face_time = 0.0
        self.frame_count = 0
        self.error_message = None
        self.last_inference_ms = 0.0
        self._inference_finished_ns = deque(maxlen=256)
        self._capture_to_snapshot_ms = deque(maxlen=256)

        # Target lock state. We keep the currently tracked face box so that
        # transient detection jitter / multi-person switches are suppressed.
        self._locked_detection = None
        self._locked_at_ns = 0
        self._lost_since_ns = None
        # 无脸计时锚点：与锁定状态解耦。锁过期后 _lost_since_ns 会被清，
        # 但“无脸时长”仍需从此前锁定的最后时刻起算，故单独保留一份。
        self._last_face_lock_expired_ns = None
        self._target_switch_count = 0
        self._target_lost_ms_samples = deque(maxlen=256)
        # Dominant-face override: if another face becomes significantly larger
        # for a few consecutive frames, switch immediately without waiting for the
        # locked target to be lost.
        self._dominant_candidate = None
        self._dominant_candidate_frames = 0

    def _load_detector(self):
        if self._detector is None:
            self._detector = create_face_detector(
                self.detector_name, self.confidence_threshold
            )
        print(f"[FACE] 人脸检测器加载完成: {self._detector.name}")

    def start(self):
        if self.running:
            return
        try:
            self._load_detector()
        except Exception as exc:
            self.error_message = str(exc)
            device_log.module_status("face", "error", self.error_message)
            print(f"[FACE] 启动失败: {exc}")
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="face-tracker"
        )
        self._thread.start()
        print(f"[FACE] 启动 detector={self._detector.name} @ {self.fps:g}fps")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        print("[FACE] 已停止")

    def get_target(self) -> tuple[float, float, bool]:
        """Compatibility interface consumed by EyeDisplay."""
        with self._lock:
            return self._target.x, self._target.y, self._target.detected

    def get_snapshot(self) -> FaceTarget:
        """Detailed latest-value interface for calibration and latency debug."""
        with self._lock:
            return self._target

    def face_absent_duration(self) -> float | None:
        """FaceTracker 判定的“无脸”时长（秒），用作入睡计时起点。

        返回 None 表示当前没有处于无脸状态（人脸可见，或从未检测到过人脸）。
        计时锚点是「最近一次持有锁定的最后时刻」：追踪器真正判定“锁定目标已
        丢失”的起点（含 target_hold_timeout 吸收期）。锁过期后锁定状态会被清，
        但无脸时长仍从该锚点累计，故单独存于 `_last_face_lock_expired_ns`。
        使用 monotonic 时钟，不受系统时间回拨影响。
        """
        with self._lock:
            anchor = (
                self._last_face_lock_expired_ns
                if self._last_face_lock_expired_ns is not None
                else self._lost_since_ns
            )
            if anchor is None:
                return None
            return (clock.monotonic_ns() - anchor) / 1_000_000_000

    def face_present_duration(self) -> float | None:
        """当前这段连续“有脸”已持续多久（秒），供唤醒去抖使用。

        锚点是 `_locked_at_ns`（本次锁定目标确立的时刻）。返回 None 表示当前无人脸。
        """
        with self._lock:
            if self._locked_detection is None:
                return None
            return (clock.monotonic_ns() - self._locked_at_ns) / 1_000_000_000

    def _update_target(self, target: FaceTarget):
        with self._lock:
            previous = self._target
            snapshot_at_ns = clock.monotonic_ns()
            if target.detected:
                entered_at_ns = (
                    target.inferred_at_ns
                    if not previous.detected
                    else previous.face_entered_at_ns
                )
                target = replace(target, face_entered_at_ns=entered_at_ns)
            # 发布时刻属于 latest snapshot 本身；它让显示端能区分“计算完成”与
            # “产品策略等待”，同时仍只保存一个目标。
            target = replace(target, snapshot_at_ns=snapshot_at_ns)
            if target.detected and target.captured_at_ns:
                self._capture_to_snapshot_ms.append(
                    (snapshot_at_ns - target.captured_at_ns) / 1_000_000
                )
            self._inference_finished_ns.append(snapshot_at_ns)
            self._target = target
            if target.detected:
                self.last_face_time = clock.time()

    @staticmethod
    def _effective_fps(timestamps: tuple[int, ...]) -> float | None:
        if len(timestamps) < 2:
            return None
        duration_s = (timestamps[-1] - timestamps[0]) / 1_000_000_000
        return round((len(timestamps) - 1) / duration_s, 3) if duration_s > 0 else None

    def get_metrics(self) -> dict:
        """Return latest-value tracker metrics for the Day 2 collector."""
        with self._lock:
            timestamps = tuple(self._inference_finished_ns)
            target = self._target
            switch_count = self._target_switch_count
            lost_ms = tuple(self._target_lost_ms_samples)
        return {
            "tracker_effective_fps": self._effective_fps(timestamps),
            "last_inference_ms": round(self.last_inference_ms, 3),
            "latest_detected": target.detected,
            "latest_inferred_at_ns": target.inferred_at_ns,
            "capture_to_snapshot_ms": self._percentiles(
                tuple(self._capture_to_snapshot_ms)
            ),
            "target_switch_count": switch_count,
            "target_lost_ms": self._percentiles(lost_ms),
        }

    @staticmethod
    def _percentiles(values: tuple[float, ...]) -> dict:
        if not values:
            return {"p50": None, "p95": None}
        ordered = sorted(values)
        return {
            "p50": round(ordered[round((len(ordered) - 1) * 0.50)], 3),
            "p95": round(ordered[round((len(ordered) - 1) * 0.95)], 3),
        }

    def process_jpeg(self, jpeg_bytes: bytes, captured_at_ns: int = 0) -> FaceTarget:
        """Decode and process one JPEG; public for deterministic tests/tools."""
        if cv2 is None:
            raise RuntimeError("opencv-python 未安装")
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("JPEG 解码失败")

        started_ns = clock.monotonic_ns()
        detections = self._detector.detect_multi(frame)
        self.last_inference_ms = (clock.monotonic_ns() - started_ns) / 1_000_000

        height, width = frame.shape[:2]
        detection = self._select_locked_detection(detections, width, height)

        if detection is None:
            previous = self.get_snapshot()
            return FaceTarget(
                previous.x, previous.y, False, 0.0, None, clock.monotonic_ns(),
                captured_at_ns=captured_at_ns,
            )

        camera_x = detection.center_x / width * 2.0 - 1.0
        camera_y = detection.center_y / height * 2.0 - 1.0
        eye_x, eye_y = self.calibration.map(camera_x, camera_y)
        return FaceTarget(
            eye_x,
            eye_y,
            True,
            detection.confidence,
            detection.box_xywh,
            detection.inferred_at_ns,
            camera_x,
            camera_y,
            captured_at_ns=captured_at_ns,
            face_area_ratio=(
                detection.box_xywh[2] * detection.box_xywh[3] / (width * height)
            ),
        )

    @staticmethod
    def _area(box_xywh: tuple[int, int, int, int]) -> int:
        return box_xywh[2] * box_xywh[3]

    def _select_locked_detection(
        self,
        detections: list | None,
        width: int,
        height: int,
    ):
        """Pick the face to track.

        Single-face scene: behave exactly like the original tracker and follow
        the only face immediately, preserving responsiveness.

        Multi-face scene: use temporal IOU locking to avoid ping-ponging between
        people. The lock is held during brief detection gaps. Switching happens
        in three cases:

        1. The locked target is lost (IOU too low) for the whole hold timeout,
           and at least one other face is still visible -> lock the largest
           remaining face immediately.
        2. Another face becomes significantly larger than the locked target for
           several consecutive frames -> switch to that dominant face.
        3. The scene collapses to a single face -> lock that face immediately.
        """
        if detections is None:
            detections = []
        now_ns = clock.monotonic_ns()

        # No face at all: keep the lock alive during the hold timeout.
        if not detections:
            if self._locked_detection is None:
                self._reset_dominant_candidate()
                return None
            if self._lost_since_ns is None:
                self._lost_since_ns = now_ns
            elapsed_ms = (now_ns - self._lost_since_ns) / 1_000_000
            self._target_lost_ms_samples.append(elapsed_ms)
            if elapsed_ms < self.target_hold_timeout * 1000:
                return self._locked_detection
            # Hold timeout expired: clear lock and report no face. 无脸计时锚点
            # 从此前锁定的最后时刻起算（与锁定状态解耦，锁清后仍累计）。
            self._last_face_lock_expired_ns = self._lost_since_ns
            self._locked_detection = None
            self._locked_at_ns = 0
            self._lost_since_ns = None
            self._reset_dominant_candidate()
            return None

        # Fresh detections available. Reset any "no face" timer.
        if self._lost_since_ns is not None:
            lost_ms = (now_ns - self._lost_since_ns) / 1_000_000
            self._target_lost_ms_samples.append(lost_ms)
            self._lost_since_ns = None
        self._last_face_lock_expired_ns = None

        # Single face: lock it immediately and reset any dominant override.
        if len(detections) == 1:
            detection = detections[0]
            if self._locked_detection is not None:
                self._locked_detection = detection
                self._locked_at_ns = now_ns
                self._reset_dominant_candidate()
                return detection
            return self._switch_target(detection, now_ns, "initial")

        # Multi-face scene.
        # No lock yet: pick the largest face and lock onto it.
        if self._locked_detection is None:
            largest = max(detections, key=lambda d: self._area(d.box_xywh))
            return self._switch_target(largest, now_ns, "initial")

        # Try to match the locked face by IOU in the new detections.
        from face_detectors import _iou
        best_iou = 0.0
        best_match = None
        for det in detections:
            iou = _iou(self._locked_detection.box_xywh, det.box_xywh)
            if iou > best_iou:
                best_iou = iou
                best_match = det

        # If another face has become significantly larger for several frames,
        # switch immediately, even though the locked target is still matched.
        largest = max(detections, key=lambda d: self._area(d.box_xywh))
        if largest is not best_match or (
            best_match is not None
            and self._area(best_match.box_xywh)
            < self._area(largest.box_xywh) * 0.99
        ):
            ratio = (
                self._area(largest.box_xywh) / self._area(self._locked_detection.box_xywh)
                if self._area(self._locked_detection.box_xywh) > 0
                else float("inf")
            )
            if ratio >= self.switch_area_ratio:
                if self._dominant_candidate is not None and self._same_detection(
                    self._dominant_candidate, largest
                ):
                    self._dominant_candidate_frames += 1
                else:
                    self._dominant_candidate = largest
                    self._dominant_candidate_frames = 1
                if self._dominant_candidate_frames >= self.dominant_switch_frames:
                    return self._switch_target(largest, now_ns, "dominant")
            else:
                self._reset_dominant_candidate()
        else:
            self._reset_dominant_candidate()

        if best_match is not None and best_iou >= self.iou_threshold:
            # Same person still visible: update lock position and continue.
            self._locked_detection = best_match
            self._locked_at_ns = now_ns
            return best_match

        # Locked face not matched. Keep the old position during hold timeout.
        if self._lost_since_ns is None:
            self._lost_since_ns = now_ns
        elapsed_ms = (now_ns - self._lost_since_ns) / 1_000_000
        self._target_lost_ms_samples.append(elapsed_ms)
        if elapsed_ms < self.target_hold_timeout * 1000:
            return self._locked_detection

        # Hold timeout expired. Lock is considered truly lost; switch to the
        # largest remaining face immediately, without requiring an area ratio.
        self._reset_dominant_candidate()
        return self._switch_target(largest, now_ns, "timeout_largest")

    @staticmethod
    def _same_detection(
        a,
        b,
        iou_threshold: float = 0.1,
    ) -> bool:
        """Check whether two detections refer to the same face."""
        from face_detectors import _iou
        return _iou(a.box_xywh, b.box_xywh) >= iou_threshold

    def _reset_dominant_candidate(self):
        """Clear the dominant-face override state."""
        self._dominant_candidate = None
        self._dominant_candidate_frames = 0

    def _switch_target(self, detection, now_ns: int, reason: str):
        """Commit to a new locked target and emit a log marker."""
        if self._locked_detection is not None:
            self._target_switch_count += 1
            print(
                f"[FACE] target switched reason={reason} "
                f"from_area={self._area(self._locked_detection.box_xywh)} "
                f"to_area={self._area(detection.box_xywh)}"
            )
        self._locked_detection = detection
        self._locked_at_ns = now_ns
        self._lost_since_ns = None
        self._pending_switch_detection = None
        self._pending_switch_since = None
        return detection

    def _loop(self):
        period = 1.0 / self.fps
        try:
            while self.running:
                loop_started = time.monotonic()
                if self.camera is not None and hasattr(self.camera, "get_frame_snapshot"):
                    jpeg, captured_at_ns = self.camera.get_frame_snapshot()
                else:
                    jpeg = self.camera.frame if self.camera is not None else None
                    captured_at_ns = 0
                if jpeg is not None:
                    try:
                        self._update_target(self.process_jpeg(jpeg, captured_at_ns))
                        self.frame_count += 1
                    except Exception as exc:
                        self.error_message = str(exc)
                        device_log.warning("人脸检测帧处理失败", error=str(exc))

                remaining = period - (time.monotonic() - loop_started)
                if remaining > 0:
                    time.sleep(remaining)
        except Exception as exc:
            self.error_message = str(exc)
            device_log.error("人脸追踪线程异常", error=str(exc))
            print(f"[FACE] 运行时错误: {exc}")
        finally:
            self.running = False
