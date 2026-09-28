"""
运动检测模块 — 从 camera.py 的 MJPEG 流解码后做 numpy 帧差分。

这样运动检测与 HTTP 视频流共享同一个 rpicam-vid 进程，避免与 picamera2
争夺相机。Pi Zero 2W 上建议把视频流分辨率设到 640x480 或更低，并由本
模块在内存中缩放到 240x180 做差分。

本模块只输出归一化的注视目标 (target_x, target_y)，由 EyeDisplay 平滑跟随。
"""

import io
import threading
import time
import numpy as np
from PIL import Image


class MotionTracker:
    """
    后台运动检测器。

    读取 CameraStreamer 提供的最新 JPEG 帧，解码、缩放后用 numpy 做帧差分，
    输出画面运动中心在 [-1, 1] 范围内的坐标。
    """

    def __init__(
        self,
        camera_streamer=None,
        width=240,
        height=180,
        fps=1 / 3,
        threshold=25,
        min_area=80,
        flip_x=False,
        flip_y=False,
    ):
        """
        Args:
            camera_streamer: CameraStreamer 实例，提供 .frame 属性 (JPEG bytes)
            width/height: 内部处理分辨率，越低越省 CPU
            fps: 采样帧率，建议 10
            threshold: 帧差阈值 (0-255)，低于此值视为噪声
            min_area: 触发运动检测的最小像素数
            flip_x/flip_y: 是否镜像坐标，用于适配摄像头安装方向
        """
        self.camera = camera_streamer
        self.width = width
        self.height = height
        self.fps = fps
        self.threshold = threshold
        self.min_area = min_area
        self.flip_x = flip_x
        self.flip_y = flip_y

        self.running = False
        self._thread = None
        self._lock = threading.Lock()

        # 输出状态
        self.target_x = 0.0
        self.target_y = 0.0
        self.motion_detected = False
        self.last_motion_time = 0.0
        self.frame_count = 0
        self.error_message = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="motion-tracker"
        )
        self._thread.start()
        print(f"[MOTION] 启动 {self.width}x{self.height} @ {self.fps}fps")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        print("[MOTION] 已停止")

    def get_target(self):
        """返回当前注视目标 (x, y) 和运动是否被检测到。"""
        with self._lock:
            return self.target_x, self.target_y, self.motion_detected

    def _update_target(self, x, y, detected):
        with self._lock:
            self.target_x = x
            self.target_y = y
            self.motion_detected = detected
            if detected:
                self.last_motion_time = time.time()

    def _decode_gray(self, jpeg_bytes):
        """把 JPEG 字节解码并缩放到固定尺寸的灰度图。"""
        img = Image.open(io.BytesIO(jpeg_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)
        arr = np.array(img, dtype=np.uint8)
        return np.mean(arr, axis=2).astype(np.uint8)

    def _loop(self):
        prev_gray = None
        period = 1.0 / self.fps
        last_time = time.time()

        try:
            while self.running:
                if self.camera is None or self.camera.frame is None:
                    time.sleep(0.1)
                    continue

                try:
                    gray = self._decode_gray(self.camera.frame)
                except Exception as e:
                    # 解码失败（如切换分辨率时）跳过这一帧
                    time.sleep(period)
                    continue

                if prev_gray is not None:
                    diff = np.abs(
                        gray.astype(np.int16) - prev_gray.astype(np.int16)
                    )
                    mask = diff > self.threshold
                    area = int(np.count_nonzero(mask))

                    if area >= self.min_area:
                        coords = np.argwhere(mask)
                        cy, cx = coords.mean(axis=0)

                        # 归一化到 [-1, 1]，左上角为 (-1,-1)，右下角为 (1,1)
                        nx = (cx / self.width) * 2.0 - 1.0
                        ny = (cy / self.height) * 2.0 - 1.0

                        if self.flip_x:
                            nx = -nx
                        if self.flip_y:
                            ny = -ny

                        self._update_target(nx, ny, True)
                    else:
                        self._update_target(self.target_x, self.target_y, False)

                prev_gray = gray
                self.frame_count += 1

                # 按帧率休眠，补偿处理耗时
                elapsed = time.time() - last_time
                sleep_time = max(0.0, period - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_time = time.time()

        except Exception as e:
            self.error_message = str(e)
            print(f"[MOTION] 运行时错误: {e}")
        finally:
            self.running = False


if __name__ == "__main__":
    print("MotionTracker 需要传入 CameraStreamer 实例，独立运行无意义。")
    print("请运行: python3 main.py --motion")
