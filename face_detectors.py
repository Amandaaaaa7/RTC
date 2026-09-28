"""Interchangeable CPU face-detector adapters for the tracking pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time

import numpy as np

try:
    import cv2
except ImportError:  # Keep imports/test discovery working off the Pi.
    cv2 = None


_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
DEFAULT_YUNET_MODEL = os.path.join(
    _MODEL_DIR, "face_detection_yunet_2023mar.onnx"
)
DEFAULT_CAFFE_PROTO = os.path.join(_MODEL_DIR, "deploy.prototxt")
DEFAULT_CAFFE_MODEL = os.path.join(
    _MODEL_DIR, "res10_300x300_ssd_iter_140000.caffemodel"
)


@dataclass(frozen=True)
class FaceDetection:
    """One detected face in source-frame coordinates."""

    box_xywh: tuple[int, int, int, int]
    center_x: float
    center_y: float
    confidence: float
    inferred_at_ns: int


def _require_cv2():
    if cv2 is None:
        raise RuntimeError("opencv-python 未安装")


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two xywh bounding boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0

    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    union_area = aw * ah + bw * bh - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def _largest_by_area(detections: list[FaceDetection]) -> FaceDetection | None:
    if not detections:
        return None
    return max(detections, key=lambda item: item.box_xywh[2] * item.box_xywh[3])


class YuNetFaceDetector:
    """OpenCV YuNet adapter. Input is a decoded BGR uint8 frame."""

    name = "yunet"

    def __init__(
        self,
        model_path: str = DEFAULT_YUNET_MODEL,
        score_threshold: float = 0.7,
        nms_threshold: float = 0.3,
        top_k: int = 5000,
        input_size: tuple[int, int] = (320, 240),
    ):
        _require_cv2()
        if not os.path.isfile(model_path):
            raise RuntimeError(
                f"YuNet 模型缺失: {model_path}。运行 bash models/download_models.sh"
            )
        self.input_size = input_size
        self._detector = cv2.FaceDetectorYN.create(
            model_path,
            "",
            input_size,
            score_threshold=score_threshold,
            nms_threshold=nms_threshold,
            top_k=top_k,
        )

    def detect(self, bgr_frame: np.ndarray) -> FaceDetection | None:
        detections = self.detect_multi(bgr_frame)
        return _largest_by_area(detections) if detections else None

    def detect_multi(self, bgr_frame: np.ndarray) -> list[FaceDetection]:
        """Return all detected faces; empty list if none."""
        height, width = bgr_frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(bgr_frame)
        if faces is None or len(faces) == 0:
            return []

        detections = []
        for face in faces:
            x, y, box_w, box_h = (int(value) for value in face[:4])
            detections.append(
                FaceDetection(
                    box_xywh=(x, y, box_w, box_h),
                    center_x=x + box_w / 2.0,
                    center_y=y + box_h / 2.0,
                    confidence=float(face[-1]),
                    inferred_at_ns=time.monotonic_ns(),
                )
            )
        return detections


class CaffeSsdFaceDetector:
    """Existing OpenCV Caffe SSD detector behind the same interface."""

    name = "caffe"

    def __init__(
        self,
        proto_path: str = DEFAULT_CAFFE_PROTO,
        model_path: str = DEFAULT_CAFFE_MODEL,
        confidence_threshold: float = 0.5,
    ):
        _require_cv2()
        if not os.path.isfile(proto_path) or not os.path.isfile(model_path):
            raise RuntimeError(
                "Caffe 人脸模型缺失。运行 bash models/download_models.sh"
            )
        self.confidence_threshold = confidence_threshold
        self._net = cv2.dnn.readNetFromCaffe(proto_path, model_path)

    def detect(self, bgr_frame: np.ndarray) -> FaceDetection | None:
        return _largest_by_area(self.detect_multi(bgr_frame))

    def detect_multi(self, bgr_frame: np.ndarray) -> list[FaceDetection]:
        """Return all detected faces; empty list if none."""
        height, width = bgr_frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(bgr_frame, (300, 300)),
            1.0,
            (300, 300),
            (104.0, 177.0, 123.0),
            swapRB=False,
            crop=False,
        )
        self._net.setInput(blob)
        raw = self._net.forward()
        detections = []
        for index in range(raw.shape[2]):
            confidence = float(raw[0, 0, index, 2])
            if confidence < self.confidence_threshold:
                continue
            x1 = int(raw[0, 0, index, 3] * width)
            y1 = int(raw[0, 0, index, 4] * height)
            x2 = int(raw[0, 0, index, 5] * width)
            y2 = int(raw[0, 0, index, 6] * height)
            box_w = max(0, x2 - x1)
            box_h = max(0, y2 - y1)
            detections.append(
                FaceDetection(
                    box_xywh=(x1, y1, box_w, box_h),
                    center_x=x1 + box_w / 2.0,
                    center_y=y1 + box_h / 2.0,
                    confidence=confidence,
                    inferred_at_ns=time.monotonic_ns(),
                )
            )
        return detections


def create_face_detector(name: str, confidence_threshold: float = 0.7):
    """Create a detector adapter by CLI/config name."""
    if name == "yunet":
        return YuNetFaceDetector(score_threshold=confidence_threshold)
    if name == "caffe":
        return CaffeSsdFaceDetector(confidence_threshold=confidence_threshold)
    raise ValueError(f"不支持的人脸检测器: {name}")
